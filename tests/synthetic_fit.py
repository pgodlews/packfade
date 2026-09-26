"""Synthetic FIT activity files for the browser-engine tests (no real rides, AGENTS.md Rule 1)."""
import struct

FIT_CRC_TABLE = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
                 0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]

# Record fields written by record_fit(): (field number, size, base type)
RECORD_FIELDS = [
    (253, 4, 134),  # timestamp (u32)
    (5, 4, 134),    # distance in cm (u32)
    (118, 1, 2),    # ebike_battery_level (u8)
    (13, 1, 1),     # temperature in C (s8)
    (73, 4, 134),   # enhanced_speed in mm/s (u32)
    (119, 1, 2),    # assist mode (u8)
]
RECORD_FORMAT = '<IIBbIB'
INVALID_U8, INVALID_S8, INVALID_U32 = 0xFF, 0x7F, 0xFFFFFFFF

# FIT timestamps count seconds from 1989-12-31T00:00:00Z
FIT_EPOCH_UNIX = 631065600
T0 = 1000000000  # 2021-09-08T01:46:40Z


def fit_crc(data):
    crc = 0
    for byte in data:
        for nibble in (byte & 0xF, byte >> 4):
            tmp = FIT_CRC_TABLE[crc & 0xF]
            crc = ((crc >> 4) & 0x0FFF) ^ tmp ^ FIT_CRC_TABLE[nibble]
    return crc


def fit_file(records, fields=RECORD_FIELDS):
    """Wrap data records (bytes after a single definition message) into a FIT file with a valid CRC."""
    def_msg = bytearray([0x40, 0, 0, 20, 0, len(fields)])
    for f_num, size, b_type in fields:
        def_msg.extend([f_num, size, b_type])
    data = def_msg + records
    header = bytearray([14, 0x10, 0, 0]) + struct.pack('<I', len(data)) + b'.FIT\x00\x00'
    body = bytes(header + data)
    return body + struct.pack('<H', fit_crc(body))


def record(t, dist_cm, soc, temp=20, speed_mms=5000, mode=7):
    return b'\x00' + struct.pack(RECORD_FORMAT, t, dist_cm, soc, temp, speed_mms, mode)


def ride_fit(socs, t0=T0, step_s=1, km_per_step=1.0, temps=None):
    """One record per SOC value, step_s apart; pass INVALID_U8 in socs for a missing battery reading."""
    temps = temps or [20] * len(socs)
    return fit_file(b''.join(
        record(t0 + i * step_s, int(i * km_per_step * 100000), soc, temp)
        for i, (soc, temp) in enumerate(zip(socs, temps))))
