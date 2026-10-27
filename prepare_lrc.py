#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""递归准备同名标准 LRC，只读取音频，不覆盖已有歌词。

默认优先选择简体来源，找不到时使用繁体；不强制转换文字，长句按小屏拆分。
用法: python prepare_lrc.py "C:\\Users\\Administrator\\Desktop\\音乐"
依赖: python -m pip install -r requirements-lyrics.txt
"""

import argparse
import base64
from functools import lru_cache
import html
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_ROOT = "https://lrclib.net/api"
USER_AGENT = "Pebby-Lyrics/1.2 (https://github.com/ZeroOneLab/pebby-lyrics)"
QQ_API = "https://u.y.qq.com/cgi-bin/musicu.fcg"
QQ_SMARTBOX_API = "https://c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg"
QQ_LYRIC_API = "https://i.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"
QQ_HEADERS = {"Referer": "https://y.qq.com/", "User-Agent": "Mozilla/5.0 (compatible; Pebby-Lyrics/1.2)"}
AUDIO_SUFFIXES = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav"}
# 允许不同抓取源的结尾时间线差异；仍按差值排序，优先更接近的版本。
DURATION_TOLERANCE = 8.0
END_TIME_TOLERANCE = 15.0
DEFAULT_MAX_LINE_CHARS = 18
MIN_SPLIT_INTERVAL_MS = 700
TIMESTAMP = re.compile(r"\[(\d{1,5}):([0-5]\d)(?:\.(\d{1,3}))?\]")
METADATA = re.compile(r"\[([a-zA-Z]+):(.*)\]")
CREDIT_LINE = re.compile(r"^(词|曲|编曲|制作人|吉他|贝斯|鼓|合声|和声|录音|混音|母带|弦乐|监制|出品|发行|作词|作曲|演唱|原唱|词曲|OP|SP|ISRC)[^：:]{0,6}[：:]", re.IGNORECASE)
TITLE_LINE = re.compile(r"^.+?\s+-\s+.+?\s*(?:\([^)]*\))?$")


@dataclass
class Track:
    path: Path
    title: str
    artist: str
    album: str
    duration: float
    metadata_source: str


@lru_cache(maxsize=1)
def chinese_converter():
    from opencc import OpenCC
    return OpenCC("t2s")


def canonical(text):
    """比较时归一简繁/全半角/空白；保留 live/remix 等版本信息。"""
    text = chinese_converter().convert(text)
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).casefold())


def text_form(lrc):
    body = "\n".join(line[match.end():] for line in lrc.splitlines()
                     if (match := TIMESTAMP.match(line)))
    return "traditional-or-mixed" if chinese_converter().convert(body) != body else "simplified-compatible"


def read_track(path):
    from mutagen import File, MutagenError

    try:
        audio = File(path, easy=True)
    except MutagenError as exc:
        raise ValueError(f"读取音频失败: {exc}") from exc
    if audio is None:
        raise ValueError("无法识别音频格式")
    tags = audio.tags or {}

    def tag_text(*keys, multiple=False):
        for key in keys:
            values = tags.get(key)
            if not values:
                continue
            values = getattr(values, "text", values)
            if not isinstance(values, (list, tuple)):
                values = [values]
            values = [str(value).strip().strip("\x00") for value in values]
            values = [value for value in values if value]
            if values:
                return " / ".join(values) if multiple else values[0]
        return ""

    title = tag_text("title", "TIT2")
    artist = tag_text("artist", "TPE1", multiple=True)
    album = tag_text("album", "TALB")
    source = "tags" if title and artist else "tags+filename"
    # 只接受有明确分隔符的“歌手 - 歌名”，不猜测普通空格的含义。
    parts = re.split(r"\s+[-–—]\s+", path.stem, maxsplit=1)
    if len(parts) == 2 and all(part.strip() for part in parts):
        if not title:
            title = parts[1].strip()
        if not artist:
            artist = parts[0].strip()
    title = title or path.stem
    duration = float(audio.info.length)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("音频时长无效，不能可靠校验歌词版本")
    return Track(path, title, artist, album, duration, source)


def fetch_json(endpoint, params, timeout, body=None, headers=None):
    url = endpoint if endpoint.startswith("https://") else API_ROOT + endpoint
    if params:
        url += "?" + urlencode(params)
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    request_headers.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    for attempt in range(2):
        time.sleep(0.3 if attempt == 0 else 1.0)
        request = Request(url, data=data, headers=request_headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = response.read(4 * 1024 * 1024 + 1)
            if len(payload) > 4 * 1024 * 1024:
                raise ValueError("歌词服务响应超过 4 MiB")
            return json.loads(payload.decode("utf-8-sig"))
        except HTTPError as exc:
            if exc.code == 404:
                return None
            if attempt == 0 and (exc.code == 429 or 500 <= exc.code < 600):
                continue
            raise
        except (URLError, TimeoutError):
            if attempt:
                raise
    raise RuntimeError("歌词服务请求未完成")


def candidate_info(record, track, duration_tolerance=DURATION_TOLERANCE):
    if not isinstance(record, dict):
        raise ValueError("歌词服务返回了无效的候选记录")
    provider = record.get("provider", "lrclib")
    record_id = record.get("id")
    if provider == "qq":
        if not isinstance(record_id, str) or not re.fullmatch(r"[A-Za-z0-9]{1,64}", record_id):
            raise ValueError("QQ 音乐返回了无效的 songmid")
        url = f"https://y.qq.com/n/ryqq/songDetail/{record_id}"
    elif provider == "lrclib" and type(record_id) is int and record_id > 0:
        url = f"{API_ROOT}/get/{record_id}"
    else:
        raise ValueError("歌词服务返回了无效的来源或记录 ID")
    for field in ("trackName", "artistName", "albumName"):
        if not isinstance(record.get(field), str):
            raise ValueError(f"歌词服务字段 {field} 无效")
    duration = float(record.get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("候选时长无效")
    synced = record.get("syncedLyrics")
    if synced is not None and not isinstance(synced, str):
        raise ValueError("同步歌词字段无效")
    reasons = []
    if canonical(record["trackName"]) != canonical(track.title):
        reasons.append("歌名不一致（保留版本后缀）")
    if canonical(record["artistName"]) != canonical(track.artist):
        reasons.append("歌手不一致")
    delta = abs(duration - track.duration)
    if delta > duration_tolerance:
        reasons.append(f"时长差超过 {duration_tolerance:g} 秒")
    return {
        "provider": provider, "id": record_id, "title": record["trackName"],
        "artist": record["artistName"], "album": record["albumName"],
        "duration": duration, "duration_delta": round(delta, 6),
        "album_match": bool(track.album and canonical(track.album) == canonical(record["albumName"])),
        "has_synced": bool(synced and synced.strip()),
        "instrumental": bool(record.get("instrumental")), "rejected": reasons, "url": url,
    }


def qq_search_musicu(query, timeout):
    """QQ 网页搜索接口；返回多条候选，失败时由调用方回退到搜索建议。"""
    body = {
        "comm": {"ct": "19", "cv": "1859", "uin": "0"},
        "req": {"module": "music.search.SearchCgiService", "method": "DoSearchForQQMusicDesktop",
                "param": {"grp": 1, "num_per_page": 10, "page_num": 1, "search_type": 0,
                          "query": query}},
    }
    data = fetch_json(QQ_API, {}, timeout, body=body, headers=QQ_HEADERS)
    if not isinstance(data, dict) or data.get("code") != 0:
        raise ValueError("QQ 搜索响应失败或格式改变")
    response = data.get("req", {})
    if not isinstance(response, dict) or response.get("code") != 0:
        code = response.get("code") if isinstance(response, dict) else "?"
        raise ValueError(f"QQ 搜索业务错误或响应格式改变（code={code}）")
    for field in ("data", "body", "song"):
        response = response.get(field)
        if not isinstance(response, dict):
            raise ValueError("QQ 搜索数据结构改变")
    songs = response.get("list")
    if not isinstance(songs, list) or not songs:
        raise ValueError("QQ 搜索没有返回歌曲列表")
    records = []
    for song in songs:
        records.append({
            "provider": "qq", "id": song["mid"], "trackName": html.unescape(song["title"]),
            "artistName": " / ".join(html.unescape(singer["name"]) for singer in song["singer"]),
            "albumName": html.unescape(song["album"]["name"]), "duration": song["interval"],
            "syncedLyrics": None, "instrumental": False,
        })
    return records


def qq_song_detail(songmid, timeout):
    """用歌曲详情补齐搜索建议缺少的专辑、时长和歌手列表。"""
    body = {"req": {"module": "music.pf_song_detail_svr", "method": "get_song_detail",
                    "param": {"song_mid": songmid}}}
    data = fetch_json(QQ_API, {}, timeout, body=body, headers=QQ_HEADERS)
    if not isinstance(data, dict) or data.get("code") != 0:
        raise ValueError("QQ 歌曲详情响应失败")
    response = data.get("req")
    if not isinstance(response, dict) or response.get("code") != 0:
        raise ValueError("QQ 歌曲详情业务错误")
    info = ((response.get("data") or {}).get("track_info"))
    if not isinstance(info, dict):
        raise ValueError("QQ 歌曲详情缺少 track_info")
    title = info.get("name") or info.get("title")
    singers = info.get("singer") or []
    artist = " / ".join(html.unescape(str(item.get("name", ""))) for item in singers
                        if isinstance(item, dict) and item.get("name"))
    album = (info.get("album") or {}).get("name") or ""
    album = str(album)
    duration = info.get("interval")
    if not isinstance(title, str) or not title or not artist:
        raise ValueError("QQ 歌曲详情缺少歌名或歌手")
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ValueError("QQ 歌曲详情时长无效")
    return {
        "provider": "qq", "id": songmid, "trackName": html.unescape(title),
        "artistName": artist, "albumName": html.unescape(album), "duration": float(duration),
        "syncedLyrics": None, "instrumental": False,
    }


def qq_search_smartbox(query, timeout):
    """搜索建议接口返回 songmid，再逐条取详情；用于搜索主接口被风控时兜底。"""
    params = {"key": query, "format": "json", "g_tk": "5381", "loginUin": "0", "hostUin": "0",
              "inCharset": "utf8", "outCharset": "utf-8", "notice": "0",
              "platform": "yqq.json", "needNewCode": "0"}
    data = fetch_json(QQ_SMARTBOX_API, params, timeout, headers=QQ_HEADERS)
    if not isinstance(data, dict) or data.get("code") != 0:
        raise ValueError("QQ 搜索建议响应失败")
    song = ((data.get("data") or {}).get("song") or {})
    items = song.get("itemlist")
    if not isinstance(items, list) or not items:
        raise ValueError("QQ 搜索建议没有返回歌曲")
    records = []
    seen = set()
    for item in items[:5]:
        songmid = item.get("mid")
        if (not isinstance(songmid, str) or not re.fullmatch(r"[A-Za-z0-9]{1,64}", songmid) or
                songmid in seen):
            continue
        seen.add(songmid)
        try:
            records.append(qq_song_detail(songmid, timeout))
        except (OSError, ValueError, TypeError, KeyError):
            continue
    if not records:
        raise ValueError("QQ 搜索建议的歌曲详情都不可用")
    return records


def qq_search(track, timeout):
    query = chinese_converter().convert(f"{track.artist} {track.title}")
    errors = []
    try:
        records = qq_search_musicu(query, timeout)
        if records:
            return records
    except (OSError, ValueError, TypeError, KeyError) as exc:
        errors.append(f"搜索接口: {exc}")
    try:
        records = qq_search_smartbox(query, timeout)
        if records:
            return records
    except (OSError, ValueError, TypeError, KeyError) as exc:
        errors.append(f"搜索建议: {exc}")
    raise ValueError("；".join(errors) or "QQ 搜索没有返回歌曲列表")


def qq_lyrics(songmid, timeout):
    data = fetch_json(QQ_LYRIC_API, {"songmid": songmid, "g_tk": 5381, "format": "json",
                      "inCharset": "utf8", "outCharset": "utf-8", "nobase64": 1}, timeout, headers=QQ_HEADERS)
    if not isinstance(data, dict) or data.get("retcode") != 0 or data.get("code", 0) != 0:
        raise ValueError("QQ 歌词响应失败，可能无歌词或接口已变动")
    lyric = data.get("lyric") or ""
    if not isinstance(lyric, str):
        raise ValueError("QQ 歌词字段无效")
    lyric = html.unescape(lyric)
    # nobase64=1 正常返回明文；兼容仍返回 Base64 的响应，不处理加密逐字歌词。
    if lyric and "[" not in lyric:
        try:
            lyric = base64.b64decode(lyric, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise ValueError("QQ 未返回可识别的明文 LRC") from None
    return lyric


def normalize_lrc(lyrics, track, end_time_tolerance=END_TIME_TOLERANCE):
    """展开多时间标签、保留空行/offset，既不猜时间也不补结束时间。"""
    rows = []
    offset = None
    for number, line in enumerate(lyrics.lstrip("\ufeff").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        timestamps = []
        pos = 0
        while match := TIMESTAMP.match(line, pos):
            minutes, seconds, fraction = match.groups()
            milliseconds = (int(minutes) * 60 + int(seconds)) * 1000
            milliseconds += int((fraction or "").ljust(3, "0"))
            timestamps.append(milliseconds)
            pos = match.end()
        if timestamps:
            text = line[pos:].strip()
            if any(ord(char) < 32 and char != "\t" for char in text):
                raise ValueError(f"第 {number} 行含控制字符")
            rows.extend((stamp, text) for stamp in timestamps)
            continue
        metadata = METADATA.fullmatch(line)
        if metadata:
            key, value = metadata.groups()
            if key.casefold() == "offset":
                if not re.fullmatch(r"[+-]?\d+", value.strip()):
                    raise ValueError("offset 不是整数毫秒")
                value = int(value)
                if offset is not None and offset != value:
                    raise ValueError("存在相互冲突的 offset")
                offset = value
            continue
        raise ValueError(f"第 {number} 行不是标准时间行或元数据，拒绝生成不完整歌词")
    if len({stamp for stamp, text in rows if text}) < 2:
        raise ValueError("没有至少两个不同时间点的非空歌词行（可能是未同步占位文本）")
    # offset 按标准标签原样保留；不同来源的结尾时间线可能差几秒，只拦截明显越界的记录。
    if max(stamp for stamp, _ in rows) > (track.duration + end_time_tolerance) * 1000 + abs(offset or 0):
        raise ValueError("歌词时间戳明显超过音频时长，可能匹配到了其他版本")

    def safe_header(text):
        return "".join(char for char in text if ord(char) >= 32).replace("[", "(").replace("]", ")")

    output = [f"[ti:{safe_header(track.title)}]", f"[ar:{safe_header(track.artist)}]"]
    if track.album:
        output.append(f"[al:{safe_header(track.album)}]")
    if offset is not None:
        output.append(f"[offset:{offset}]")
    rows.sort(key=lambda row: row[0])
    for stamp, text in rows:
        minutes, remainder = divmod(stamp, 60000)
        seconds, milliseconds = divmod(remainder, 1000)
        output.append(f"[{minutes:02d}:{seconds:02d}.{milliseconds:03d}]{text}")
    return "\n".join(output) + "\n", len(rows)


def find_lyrics(track, timeout, source="auto", prefer="simplified",
                duration_tolerance=DURATION_TOLERANCE, end_time_tolerance=END_TIME_TOLERANCE):
    """同专辑、用户简繁优先级、QQ 来源、时长和行长优先。"""
    preferred_form = "traditional-or-mixed" if prefer == "traditional" else "simplified-compatible"
    details = {"candidates": [], "warnings": [], "sources": {}}
    eligible = []
    network_failed = False

    def add_record(record, method):
        info = candidate_info(record, track, duration_tolerance)
        key = (info["provider"], info["id"])
        if any((item["provider"], item["id"]) == key for item in details["candidates"]):
            return
        info["match_method"] = method
        details["candidates"].append(info)
        if info["rejected"]:
            return
        if info["provider"] == "qq":
            record["syncedLyrics"] = qq_lyrics(info["id"], timeout)
            info["has_synced"] = bool(record["syncedLyrics"].strip())
        if not info["has_synced"] or info["instrumental"]:
            return
        try:
            text, count = normalize_lrc(record["syncedLyrics"], track, end_time_tolerance)
        except ValueError as exc:
            info["invalid_lrc"] = str(exc)
            details["warnings"].append(f"{info['provider']} {info['id']}: {exc}")
            return
        info["text_form"] = text_form(text)
        info["line_count"] = count
        body_lines = [line[match.end():].strip() for line in text.splitlines()
                      if (match := TIMESTAMP.match(line))]
        info["max_line_chars"] = max((len(line) for line in body_lines), default=0)
        eligible.append((info, text, count))

    def add_safely(record, method):
        nonlocal network_failed
        try:
            add_record(record, method)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            network_failed = True
            details["warnings"].append(f"{method} 候选处理失败: {exc}")

    if source in {"auto", "qq"}:
        try:
            records = qq_search(track, timeout)
            for record in records:
                add_safely(record, "qq-search")
            details["sources"]["qq"] = "queried"
        except (OSError, ValueError, TypeError, KeyError) as exc:
            network_failed = True
            details["sources"]["qq"] = "error"
            details["warnings"].append(f"QQ 查询失败: {exc}")
    # 已有符合优先级的 QQ 结果时无需继续请求；只有另一种文字形态则继续找兜底来源。
    qq_preferred = any(info["provider"] == "qq" and info["text_form"] == preferred_form
                       for info, _, _ in eligible)
    if source == "lrclib" or (source == "auto" and not qq_preferred):
        exact_preferred = False
        if track.album:
            try:
                exact = fetch_json("/get", {"track_name": track.title, "artist_name": track.artist,
                                   "album_name": track.album, "duration": round(track.duration)}, timeout)
                if exact is not None:
                    add_safely(exact, "lrclib-get")
                exact_preferred = any(info["provider"] == "lrclib" and info["album_match"] and
                                      info["text_form"] == preferred_form for info, _, _ in eligible)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                network_failed = True
                details["warnings"].append(f"LRCLIB 精确查询失败，尝试搜索: {exc}")
        if not exact_preferred:
            try:
                records = fetch_json("/search", {"track_name": track.title, "artist_name": track.artist}, timeout)
                if not isinstance(records, list):
                    raise ValueError("返回的不是候选列表")
                for record in records:
                    add_safely(record, "lrclib-search")
                details["sources"]["lrclib"] = "queried"
            except (OSError, ValueError, TypeError, KeyError) as exc:
                network_failed = True
                details["sources"]["lrclib"] = "error"
                details["warnings"].append(f"LRCLIB 搜索失败: {exc}")
        else:
            details["sources"]["lrclib"] = "queried"
    if not eligible:
        matching = [item for item in details["candidates"] if not item["rejected"]]
        if network_failed:
            return "network_error", "未取得可用同步歌词，且有来源请求/响应失败；详见警告", None, details
        if any(item.get("invalid_lrc") for item in matching):
            return "invalid_lrc", "匹配候选的同步歌词不合法，未写入", None, details
        if matching:
            status = "instrumental" if all(item["instrumental"] for item in matching) else "no_synced"
            return status, "匹配候选被标记为纯音乐或没有同步歌词；不生成时间轴", None, details
        return "no_match", f"没有同时满足歌名、歌手及 {DURATION_TOLERANCE:g} 秒时长差的候选", None, details
    # 先满足同专辑，再按用户选择的简繁优先级；同一文字形态下优先 QQ、再比时长和行长。
    # 简繁是“有谁先用谁”的优先级，找不到时接受另一种，不在这里强制转换。
    eligible.sort(key=lambda item: (not item[0]["album_match"],
                  item[0]["text_form"] != preferred_form,
                  item[0]["provider"] != "qq",
                  item[0]["duration_delta"],
                  item[0]["max_line_chars"],
                  -item[0]["line_count"], str(item[0]["id"])))
    best, text, count = eligible[0]
    for other, other_text, _ in eligible[1:]:
        if (other["provider"] == best["provider"] and other["text_form"] == best["text_form"] and
                other["album_match"] == best["album_match"] and
                other["duration_delta"] - best["duration_delta"] <= 0.1 and other_text != text):
            return "ambiguous", "存在近似同分但时间轴/文本不同的候选，请查看报告，未自动写入", None, details
    details.update(selected=best, match_method=best["match_method"], line_count=count,
                   max_line_chars=best["max_line_chars"], preferred_form=preferred_form)
    actual_form = "简体" if best["text_form"] == "simplified-compatible" else "繁体"
    wanted_form = "简体" if prefer == "simplified" else "繁体"
    fallback = "" if actual_form == wanted_form else f"（未找到{wanted_form}，使用{actual_form}）"
    return "matched", f"{best['provider']} 匹配；来源{actual_form}{fallback}；请试听确认版本和偏移", text, details


def existing_lrc(audio_path):
    name = audio_path.with_suffix(".lrc").name.casefold()
    return next((item for item in audio_path.parent.iterdir() if item.name.casefold() == name), None)


def write_exclusive(path, payload):
    # x 模式避免检查后出现新文件时覆盖；磁盘满或中断时清掉本次创建的半文件。
    stream = path.open("xb")
    try:
        with stream:
            stream.write(payload)
    except BaseException:
        path.unlink()
        raise


def write_replace(path, payload):
    """覆盖写入：同目录临时文件 + os.replace，失败时保留旧文件。"""
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except BaseException:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def format_lrc_time(milliseconds):
    minutes, remainder = divmod(milliseconds, 60000)
    seconds, ms = divmod(remainder, 1000)
    return f"[{minutes:02d}:{seconds:02d}.{ms:03d}]"


def drop_leading_credits(lrc):
    """移除 QQ 片头的“歌名 - 歌手”和词曲/制作信息行，保留正文。"""
    headers = []
    rows = []
    for line in lrc.splitlines():
        if not line.strip():
            continue
        match = TIMESTAMP.match(line)
        if match:
            stamps = []
            pos = 0
            while match := TIMESTAMP.match(line, pos):
                minutes, seconds, fraction = match.groups()
                stamp = (int(minutes) * 60 + int(seconds)) * 1000
                stamp += int((fraction or "").ljust(3, "0"))
                stamps.append(stamp)
                pos = match.end()
            text = line[pos:].strip()
            rows.extend((stamp, text) for stamp in stamps)
        elif METADATA.fullmatch(line):
            headers.append(line)
        else:
            return lrc, 0
    removed = 0
    while rows and not rows[0][1]:
        rows.pop(0)
    while rows and (TITLE_LINE.match(rows[0][1]) or CREDIT_LINE.match(rows[0][1])):
        rows.pop(0)
        removed += 1
    if not removed or not rows:
        return lrc, 0
    rebuilt = headers + [f"{format_lrc_time(stamp)}{text}" for stamp, text in rows]
    return "\n".join(rebuilt) + "\n", removed


def split_text_line(text, max_chars):
    """按空格/标点切分长句，每段不超过 max_chars；不修改文字本身。"""
    text = text.strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]
    if CREDIT_LINE.match(text) or TITLE_LINE.match(text):
        return [text]
    separators = " ，。！？、；：,.!?;:"
    chunks = []
    rest = text
    while len(rest) > max_chars:
        window = rest[:max_chars]
        choices = [index for index, char in enumerate(window) if index >= 4 and char in separators]
        if choices:
            target = len(rest) / 2
            cut = min(choices, key=lambda index: (abs(index - target), -index))
        else:
            cut = max_chars
        chunk = rest[:cut].strip()
        if chunk:
            chunks.append(chunk)
        rest = rest[cut:].strip()
    if rest:
        chunks.append(rest)
    return chunks


def split_long_lines(lrc, track, max_chars):
    """把超长行拆成短句；同一行内按字数和下一行时间线性分配，时间戳为估算值。"""
    if max_chars <= 0:
        return lrc, 0
    headers = []
    rows = []
    for line in lrc.splitlines():
        if not line.strip():
            continue
        match = TIMESTAMP.match(line)
        if match:
            stamps = []
            pos = 0
            while match := TIMESTAMP.match(line, pos):
                minutes, seconds, fraction = match.groups()
                stamp = (int(minutes) * 60 + int(seconds)) * 1000
                stamp += int((fraction or "").ljust(3, "0"))
                stamps.append(stamp)
                pos = match.end()
            text = line[pos:].strip()
            rows.extend((stamp, text) for stamp in stamps)
        elif METADATA.fullmatch(line):
            headers.append(line)
        else:
            return lrc, 0
    if not rows:
        return lrc, 0
    rows.sort(key=lambda row: row[0])
    output = []
    split_count = 0
    for index, (stamp, text) in enumerate(rows):
        if not text or len(text) <= max_chars:
            output.append((stamp, text))
            continue
        next_stamp = next((time_value for time_value, _ in rows[index + 1:] if time_value > stamp),
                          max(int(track.duration * 1000), stamp + 1000))
        chunks = split_text_line(text, max_chars)
        span = next_stamp - stamp
        if len(chunks) <= 1 or span < len(chunks) * MIN_SPLIT_INTERVAL_MS:
            output.append((stamp, text))
            continue
        weights = [max(len(chunk), 1) for chunk in chunks]
        total = sum(weights)
        candidate = []
        consumed = 0
        for chunk_index, chunk in enumerate(chunks):
            if chunk_index == 0:
                candidate_stamp = stamp
            else:
                candidate_stamp = stamp + round(span * consumed / total)
                candidate_stamp = max(candidate_stamp,
                                      candidate[-1][0] + MIN_SPLIT_INTERVAL_MS)
                candidate_stamp = min(candidate_stamp, next_stamp - MIN_SPLIT_INTERVAL_MS)
            candidate.append((candidate_stamp, chunk))
            consumed += weights[chunk_index]
        if (all(candidate[i][0] < candidate[i + 1][0] for i in range(len(candidate) - 1)) and
                candidate[-1][0] < next_stamp):
            output.extend(candidate)
            split_count += len(candidate) - 1
        else:
            output.append((stamp, text))
    rebuilt = headers + [f"{format_lrc_time(stamp)}{text}" for stamp, text in output]
    return "\n".join(rebuilt) + "\n", split_count


def process_file(path, encoding, dry_run, timeout, source="auto", prefer="simplified", convert=False,
                 max_line_chars=DEFAULT_MAX_LINE_CHARS, drop_credits=True, overwrite=False,
                 duration_tolerance=DURATION_TOLERANCE, end_time_tolerance=END_TIME_TOLERANCE):
    result = {"audio": str(path), "lrc": str(path.with_suffix(".lrc")), "encoding": encoding,
              "overwrite": overwrite}
    try:
        existing = existing_lrc(path)
        if existing is not None and not overwrite:
            result.update(status="exists", message="已有同名 LRC，保留且不联网", lrc=str(existing))
            return result
        track = read_track(path)
        result["local"] = {"title": track.title, "artist": track.artist, "album": track.album,
                           "duration": track.duration, "metadata_source": track.metadata_source}
        if not track.artist:
            result.update(status="missing_metadata", message="缺少歌手标签，文件名也不是“歌手 - 歌名”；请补标签后重试")
            return result
        status, message, text, details = find_lyrics(track, timeout, source, prefer,
                                                     duration_tolerance, end_time_tolerance)
        result.update(details, status=status, message=message, prefer=prefer)
        if text is None:
            return result
        if drop_credits:
            text, credits_removed = drop_leading_credits(text)
            if credits_removed:
                result["credits_removed"] = credits_removed
                result["line_count"] = sum(1 for line in text.splitlines()
                                           if TIMESTAMP.match(line))
                result["message"] += f"；已移除 {credits_removed} 行片头信息"
        result["text_processing"] = "original"
        if convert:
            converted = chinese_converter().convert(text)
            if converted != text:
                result["text_processing"] = "traditional-to-simplified"
                result["message"] += "；已强制转简体（保留时间戳）"
            text = converted
        text, split_count = split_long_lines(text, track, max_line_chars)
        if split_count:
            result["line_processing"] = {"max_line_chars": max_line_chars,
                                         "split_lines": split_count}
            result["message"] += f"；已拆分 {split_count} 行长句"
        body_lines = [line[match.end():].strip() for line in text.splitlines()
                      if (match := TIMESTAMP.match(line))]
        result["line_count"] = len(body_lines)
        result["max_line_chars"] = max((len(line) for line in body_lines), default=0)
        try:
            payload = text.encode(encoding, errors="strict")
        except UnicodeEncodeError as exc:
            result.update(status="encoding_error", message=f"歌词无法无损编码为 {encoding}，请使用 UTF-8: {exc}")
            return result
        if dry_run:
            result.update(status="dry_run", message=result["message"] + "；预览未写文件")
            return result
        try:
            if overwrite:
                write_replace(path.with_suffix(".lrc"), payload)
                result["overwritten"] = existing is not None
            else:
                write_exclusive(path.with_suffix(".lrc"), payload)
        except FileExistsError:
            result.update(status="exists", message="写入前已出现 LRC，保留且不覆盖")
            return result
        result.update(status="saved")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result.update(status="error", message=f"处理失败: {type(exc).__name__}: {exc}")
    return result


def scan_audio(root, errors, recursive=True, suffixes=None):
    allowed = AUDIO_SUFFIXES if suffixes is None else {str(item).casefold() for item in suffixes}
    if root.is_file():
        return [root] if root.suffix.casefold() in allowed else []
    files = []
    if not recursive:
        for path in sorted(root.iterdir()):
            if path.is_file() and not path.is_symlink() and path.suffix.casefold() in allowed:
                files.append(path)
        return files
    for directory, dirs, names in os.walk(root, onerror=lambda exc: errors.append(str(exc)), followlinks=False):
        dirs[:] = sorted(name for name in dirs if not (Path(directory) / name).is_symlink())
        for name in sorted(names):
            path = Path(directory) / name
            if path.suffix.casefold() in allowed and not path.is_symlink():
                files.append(path)
    return files


def positive_timeout(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("超时必须是正数秒")
    return value


def nonnegative_chars(value):
    value = int(value)
    if value < 0:
        raise argparse.ArgumentTypeError("行长阈值不能是负数")
    return value


def secure_output() -> None:
    """把输出改成能容纳中文的编码。

    Windows 旧代码页（如 CI 的 cp1252）下把中文写进管道/重定向会直接抛
    UnicodeEncodeError；非交互输出统一改用 UTF-8 并容错，交互式控制台保持原样。
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def main():
    secure_output()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    try:
        from version import __version__ as app_version
    except ImportError:
        app_version = "unknown"
    parser.add_argument("--version", action="version", version=f"Pebby Lyrics {app_version}")
    parser.add_argument("root", nargs="?", type=Path, default=Path.home() / "Desktop" / "音乐", help="音乐目录或单个音频文件")
    parser.add_argument("--encoding", choices=("utf-8", "gbk"), default="utf-8", help="输出编码，默认 UTF-8 无 BOM")
    parser.add_argument("--source", choices=("auto", "qq", "lrclib"), default="auto", help="默认 QQ 优先、LRCLIB 兜底，同专辑/简繁优先级/来源/时长优先")
    parser.add_argument("--prefer", choices=("simplified", "traditional"), default="simplified",
                        help="优先使用简体还是繁体来源；找不到时用另一种，默认简体")
    parser.add_argument("--keep-traditional", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--keep-credits", action="store_true", help="保留 QQ 片头的歌名/词曲信息行；默认移除")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已有同名 LRC；默认跳过")
    parser.add_argument("--simplify", action="store_true", help="可选：选中后强制把文本转简体；默认只按优先级选择，不转换")
    parser.add_argument("--max-line-chars", type=nonnegative_chars, default=DEFAULT_MAX_LINE_CHARS,
                        help=f"长句拆分阈值（字符），默认 {DEFAULT_MAX_LINE_CHARS}；0 不拆分")
    parser.add_argument("--dry-run", action="store_true", help="查询并校验，但不写 LRC")
    parser.add_argument("--report", type=Path, help="可选的 UTF-8 JSON 结果报告（不含歌词正文）")
    parser.add_argument("--timeout", type=positive_timeout, default=15, help="单次请求超时秒数，默认 15")
    args = parser.parse_args()
    prefer = "traditional" if args.keep_traditional else args.prefer
    convert = args.simplify
    drop_credits = not args.keep_credits
    root = args.root.expanduser().resolve()
    if not root.exists():
        parser.error(f"路径不存在: {root}")
    try:
        from mutagen import File
        chinese_converter()
    except ImportError:
        parser.error("缺少 Mutagen/OpenCC，请先运行 prepare_lrc.cmd 安装依赖，或执行: python -m pip install -r requirements-lyrics.txt")
    errors = []
    files = scan_audio(root, errors)
    print(f"扫描: {root}\n音频: {len(files)} 个；编码: {args.encoding}；"
          f"优先{'简体' if prefer == 'simplified' else '繁体'}；"
          f"{'强制转简体' if convert else '保留来源文字'}；"
          f"行长≤{args.max_line_chars or '不限'}；"
          f"{'移除片头信息' if drop_credits else '保留片头信息'}；"
          f"{'覆盖已有 LRC' if args.overwrite else '已有 LRC 不覆盖'}")
    results = []
    try:
        for path in files:
            result = process_file(path, args.encoding, args.dry_run, args.timeout, args.source,
                                  prefer, convert, args.max_line_chars, drop_credits,
                                  overwrite=args.overwrite)
            results.append(result)
            print(f"[{result['status']}] {path.name}: {result['message']}")
            selected = result.get("selected")
            if selected:
                print(f"  {selected['provider']} ID={selected['id']} | {selected['artist']} - {selected['title']} | 专辑={selected['album']} | 时长差={selected['duration_delta']:.3f}s | {result['line_count']} 行")
            for warning in result.get("warnings", []):
                print(f"  警告: {warning}")
    except KeyboardInterrupt:
        errors.append("用户中断；已完成文件保留，未完成文件不视为成功")
    for error in errors:
        print(f"[error] {error}", file=sys.stderr)
    summary = dict(Counter(result["status"] for result in results))
    print("汇总: " + (", ".join(f"{status}={count}" for status, count in summary.items()) or "无音频文件"))
    if args.report:
        report = {"created_at": datetime.now().astimezone().isoformat(), "root": str(root),
                  "encoding": args.encoding, "source": args.source, "prefer": prefer,
                  "convert": convert, "max_line_chars": args.max_line_chars,
                  "drop_credits": drop_credits, "overwrite": args.overwrite,
                  "dry_run": args.dry_run, "summary": summary,
                  "scan_errors": errors, "results": results}
        try:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            # 报告也不覆盖已有文件，避免误把音频或现有 LRC 路径当成报告输出。
            write_exclusive(args.report, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            print(f"报告: {args.report.resolve()}")
        except OSError as exc:
            print(f"[error] 报告写入失败: {exc}", file=sys.stderr)
            return 2
    if errors:
        return 2
    return 1 if any(result["status"] not in {"saved", "exists", "dry_run"} for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
