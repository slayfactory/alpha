//! Целые переменной длины: uvarint и zigzag.

use super::*;

// ---------------------------------------------------------------------
// Varint: без знака (LEB128) и зигзаг для знаковых дельт.
// ---------------------------------------------------------------------

/// LEB128 без знака. `Vec::push` — единственная операция роста, поэтому
/// после прогрева (`Vec` уже нужной ёмкости) вызов не аллоцирует.
pub(super) fn write_uvarint(buf: &mut Vec<u8>, mut v: u64) {
    loop {
        let byte = (v & 0x7f) as u8;
        v >>= 7;
        if v == 0 {
            buf.push(byte);
            return;
        }
        buf.push(byte | 0x80);
    }
}

/// Зигзаг переставляет знаковые значения так, чтобы малые по модулю (в т.ч.
/// отрицательные) давали короткий varint — ровно то, что нужно дельтам,
/// которые в среднем малы и колеблются вокруг нуля в обе стороны.
pub(super) fn zigzag_encode(v: i64) -> u64 {
    ((v << 1) ^ (v >> 63)) as u64
}

pub(super) fn zigzag_decode(v: u64) -> i64 {
    ((v >> 1) as i64) ^ -((v & 1) as i64)
}

pub(super) fn write_zigzag(buf: &mut Vec<u8>, v: i64) {
    write_uvarint(buf, zigzag_encode(v));
}

/// Читает varint из `buf`, начиная с `*pos`, и продвигает `*pos`. Ошибка —
/// не паника: `buf.get` вместо индексации, явный предел на число байт,
/// потому что оборванный кадр обязан дать `Corrupt`, а не читать за границей.
#[inline(always)]
pub(super) fn read_uvarint(buf: &[u8], pos: &mut usize) -> Result<u64, BinlogError> {
    if let Some([b0, b1]) = buf.get(*pos..*pos + 2) {
        if *b0 < 0x80 {
            *pos += 1;
            return Ok(u64::from(*b0));
        }
        if *b1 < 0x80 {
            *pos += 2;
            return Ok(u64::from(*b0 & 0x7f) | (u64::from(*b1) << 7));
        }
    } else if let Some(&b) = buf.get(*pos) {
        if b < 0x80 {
            *pos += 1;
            return Ok(u64::from(b));
        }
    }
    read_uvarint_slow(buf, pos)
}

#[inline(never)]
pub(super) fn read_uvarint_slow(buf: &[u8], pos: &mut usize) -> Result<u64, BinlogError> {
    let mut result: u64 = 0;
    let mut shift = 0u32;
    loop {
        let byte = *buf
            .get(*pos)
            .ok_or_else(|| BinlogError::Corrupt("varint обрезан".to_string()))?;
        *pos += 1;
        if shift >= 64 {
            return Err(BinlogError::Corrupt("varint длиннее 64 бит".to_string()));
        }
        result |= ((byte & 0x7f) as u64) << shift;
        if byte & 0x80 == 0 {
            return Ok(result);
        }
        shift += 7;
    }
}

pub(super) fn read_zigzag(buf: &[u8], pos: &mut usize) -> Result<i64, BinlogError> {
    read_uvarint(buf, pos).map(zigzag_decode)
}
