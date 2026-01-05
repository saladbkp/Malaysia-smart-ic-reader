import sys
import time
from datetime import datetime
from smartcard.System import readers
import os
import json
import base64
import argparse


def transmit_apdu(conn, cla, ins, p1, p2, data=None, le=None):
    apdu = [cla, ins, p1, p2]
    if data is not None:
        apdu.append(len(data))
        apdu.extend(data)
    if le is not None:
        apdu.append(le)
    response, sw1, sw2 = conn.transmit(apdu)
    return bytes(response), sw1, sw2


def le_short(x):
    return [x & 0xFF, (x >> 8) & 0xFF]


def select_application(conn):
    aid = [0xA0, 0x00, 0x00, 0x00, 0x74, 0x4A, 0x50, 0x4E, 0x00, 0x10]
    _, sw1, sw2 = transmit_apdu(conn, 0x00, 0xA4, 0x04, 0x00, aid, 0x00)
    return (sw1 == 0x90 and sw2 == 0x00) or sw1 in (0x61, 0x90)


def set_length(conn, length):
    payload = [0x08, 0x00, 0x00] + le_short(length)
    _, sw1, sw2 = transmit_apdu(conn, 0xC8, 0x32, 0x00, 0x00, payload, 0x00)
    if not (sw1 == 0x91 and sw2 == 0x08):
        raise RuntimeError("Failed to set length")


def select_info(conn, filen1, filen2, offset, length):
    data = le_short(filen2) + le_short(filen1) + le_short(offset) + le_short(length)
    _, sw1, _ = transmit_apdu(conn, 0xCC, 0x00, 0x00, 0x00, data, 0x00)
    if sw1 != 0x94:
        raise RuntimeError("Failed to select info")


def read_info(conn, length):
    out = bytearray()
    remaining = length
    while remaining > 0:
        chunk = remaining if remaining < 0xFF else 0xFF
        data, sw1, _ = transmit_apdu(conn, 0xCC, 0x06, 0x00, 0x00, None, chunk)
        if sw1 not in (0x94, 0x90):
            raise RuntimeError("Failed to read info")
        out.extend(data)
        remaining -= chunk
    return bytes(out)


def convert_bcd_date(data, offset):
    year = 0
    for i in range(offset, offset + 2):
        year = year * 100 + ((data[i] >> 4) & 0xF) * 10 + (data[i] & 0xF)
    month = ((data[offset + 2] >> 4) & 0xF) * 10 + (data[offset + 2] & 0xF)
    day = ((data[offset + 3] >> 4) & 0xF) * 10 + (data[offset + 3] & 0xF)
    return datetime(year, month, day)


def convert_bcd_postcode(data, offset):
    n = 0
    for i in range(offset, offset + 3):
        n = n * 100 + ((data[i] >> 4) & 0xF) * 10 + (data[i] & 0xF)
    return n // 10


def parse_text(data, start, length):
    return bytes(data[start:start + length]).decode("ascii", errors="ignore").strip()


def main():
    rs = readers()
    if not rs:
        print("No smart card readers found")
        sys.exit(1)
    reader = rs[0]
    conn = reader.createConnection()
    try:
        conn.connect()
    except Exception:
        print("No card present. Please insert MyKad and try again.")
        sys.exit(2)

    if not select_application(conn):
        print("Failed to select MyKad application (AID)")
        sys.exit(3)

    jpn1_1_len = 459
    jpn1_2_len = 4011
    jpn1_4_len = 171

    set_length(conn, jpn1_1_len)
    select_info(conn, 1, 1, 0, jpn1_1_len)
    jpn1_1 = read_info(conn, jpn1_1_len)

    name = parse_text(jpn1_1, 3, 150)
    ic = parse_text(jpn1_1, 273, 13)
    sex = "Male" if jpn1_1[286:287].decode("ascii", errors="ignore") == "L" else "Female"
    old_ic = parse_text(jpn1_1, 287, 8)
    birth_date = convert_bcd_date(jpn1_1, 295)
    birth_place = parse_text(jpn1_1, 299, 25)
    issue_date = convert_bcd_date(jpn1_1, 324)
    citizenship = parse_text(jpn1_1, 328, 18)
    race = parse_text(jpn1_1, 346, 25)
    religion = parse_text(jpn1_1, 371, 11)

    set_length(conn, jpn1_2_len)
    select_info(conn, 1, 2, 0, jpn1_2_len)
    jpn1_2 = read_info(conn, jpn1_2_len)
    photo_bytes = jpn1_2[3:3 + 4000]

    set_length(conn, jpn1_4_len)
    select_info(conn, 1, 4, 0, jpn1_4_len)
    jpn1_4 = read_info(conn, jpn1_4_len)
    address1 = parse_text(jpn1_4, 3, 30)
    address2 = parse_text(jpn1_4, 33, 30)
    address3 = parse_text(jpn1_4, 63, 30)
    postcode = convert_bcd_postcode(jpn1_4, 93)
    city = parse_text(jpn1_4, 96, 25)
    state = parse_text(jpn1_4, 121, 30)

    result = {
        "IC": ic,
        "Name": name,
        "Sex": sex,
        "OldIC": old_ic,
        "BirthDate": birth_date.strftime("%d %b %Y"),
        "BirthPlace": birth_place,
        "IssueDate": issue_date.strftime("%d %b %Y"),
        "Citizenship": citizenship,
        "Race": race,
        "Religion": religion,
        "Address1": address1,
        "Address2": address2,
        "Address3": address3,
        "Postcode": str(postcode),
        "City": city,
        "State": state,
        "PhotoLength": len(photo_bytes),
    }

    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.json:
        payload = {
            "IC": ic,
            "Name": name,
            "Sex": sex,
            "OldIC": old_ic,
            "BirthDate": result["BirthDate"],
            "BirthPlace": birth_place,
            "IssueDate": result["IssueDate"],
            "Citizenship": citizenship,
            "Race": race,
            "Religion": religion,
            "Address1": address1,
            "Address2": address2,
            "Address3": address3,
            "Postcode": result["Postcode"],
            "City": city,
            "State": state,
            "PhotoDataUrl": "data:image/jpeg;base64," + base64.b64encode(photo_bytes).decode("ascii")
        }
        print(json.dumps(payload, ensure_ascii=False))
        return
    else:
        print("MyKad Read Success")
        for k, v in result.items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
