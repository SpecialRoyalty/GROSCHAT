from __future__ import annotations

import asyncio
import hashlib
import io
from dataclasses import dataclass

from aiogram import Bot
from aiogram.types import Message
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select, update

from app.db.models import MediaHash, PerceptualHash
from app.db.session import SessionLocal

# Les fichiers volumineux (surtout les longues vidéos) ne sont pas téléchargés
# intégralement pour calculer leur SHA-256. Leur miniature Telegram est utilisée
# pour l'empreinte visuelle, ce qui garde la modération rapide.
MAX_SHA256_BYTES = 20 * 1024 * 1024
IMAGE_DISTANCE_LIMIT = 10
VIDEO_DISTANCE_LIMIT = 12


@dataclass(frozen=True)
class MediaEntry:
    unique_id: str
    file_id: str
    media_type: str
    file_size: int | None = None
    preview_file_id: str | None = None
    visual_type: str | None = None


def _thumbnail_file_id(media) -> str | None:
    thumb = getattr(media, "thumbnail", None) or getattr(media, "thumb", None)
    return getattr(thumb, "file_id", None) if thumb else None


def media_file_entries(msg: Message) -> list[MediaEntry]:
    if msg.photo:
        photo = msg.photo[-1]
        return [MediaEntry(photo.file_unique_id, photo.file_id, "photo", photo.file_size, photo.file_id, "image")]
    if msg.video:
        return [MediaEntry(msg.video.file_unique_id, msg.video.file_id, "video", msg.video.file_size, _thumbnail_file_id(msg.video), "video")]
    if msg.animation:
        return [MediaEntry(msg.animation.file_unique_id, msg.animation.file_id, "animation", msg.animation.file_size, _thumbnail_file_id(msg.animation), "video")]
    if msg.video_note:
        return [MediaEntry(msg.video_note.file_unique_id, msg.video_note.file_id, "video_note", msg.video_note.file_size, _thumbnail_file_id(msg.video_note), "video")]
    if msg.document:
        mime = (msg.document.mime_type or "").lower()
        preview = msg.document.file_id if mime.startswith("image/") else _thumbnail_file_id(msg.document)
        visual_type = "image" if mime.startswith("image/") else ("video" if mime.startswith("video/") else None)
        return [MediaEntry(msg.document.file_unique_id, msg.document.file_id, "document", msg.document.file_size, preview, visual_type)]
    return []


async def _download_bytes(bot: Bot, file_id: str) -> bytes | None:
    try:
        bio = await bot.download(file_id)
        if not bio:
            return None
        bio.seek(0)
        return bio.read()
    except Exception:
        return None


async def file_sha256(bot: Bot, file_id: str, file_size: int | None = None) -> str | None:
    if file_size and file_size > MAX_SHA256_BYTES:
        return None
    raw = await _download_bytes(bot, file_id)
    if not raw:
        return None
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _difference_hash(image: Image.Image, size: int = 8) -> str:
    gray = ImageOps.grayscale(image).resize((size + 1, size), Image.Resampling.LANCZOS)
    pixels = list(gray.getdata())
    bits = []
    row_width = size + 1
    for y in range(size):
        row = pixels[y * row_width:(y + 1) * row_width]
        bits.extend(row[x] > row[x + 1] for x in range(size))
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def _visual_fingerprints_sync(raw: bytes) -> list[tuple[str, str]]:
    try:
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            width, height = image.size
            if width < 16 or height < 16:
                return []

            variants: list[tuple[str, Image.Image]] = [("full", image)]

            # Une empreinte du centre résiste mieux aux petits recadrages,
            # bordures et watermarks placés sur les côtés.
            margin_x = max(1, int(width * 0.10))
            margin_y = max(1, int(height * 0.10))
            if width - 2 * margin_x >= 16 and height - 2 * margin_y >= 16:
                variants.append(("center", image.crop((margin_x, margin_y, width - margin_x, height - margin_y))))

            return [(variant, _difference_hash(candidate)) for variant, candidate in variants]
    except (UnidentifiedImageError, OSError, ValueError):
        return []


async def visual_fingerprints(bot: Bot, entry: MediaEntry) -> list[tuple[str, str]]:
    preview_id = entry.preview_file_id
    if not preview_id or not entry.visual_type:
        return []
    raw = await _download_bytes(bot, preview_id)
    if not raw:
        return []
    return await asyncio.to_thread(_visual_fingerprints_sync, raw)


def _buckets(hash_value: str) -> tuple[str, str, str, str]:
    # 64 bits répartis en 4 morceaux. La requête SQL ne charge que les
    # candidats partageant au moins un morceau, au lieu de scanner la table.
    normalized = hash_value.zfill(16)[-16:]
    return normalized[0:4], normalized[4:8], normalized[8:12], normalized[12:16]


def _hamming(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _distance_limit(media_type: str) -> int:
    return VIDEO_DISTANCE_LIMIT if media_type in {"video", "animation", "video_note"} else IMAGE_DISTANCE_LIMIT


async def _save_visual_hashes(
    db,
    *,
    user_id: int | None,
    media_type: str,
    fingerprints: list[tuple[str, str]],
    banned: bool,
) -> int:
    saved = 0
    for variant, hash_value in fingerprints:
        b0, b1, b2, b3 = _buckets(hash_value)
        result = await db.execute(
            select(PerceptualHash).where(
                PerceptualHash.user_id == user_id,
                PerceptualHash.media_type == media_type,
                PerceptualHash.variant == variant,
                PerceptualHash.hash_value == hash_value,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = PerceptualHash(
                user_id=user_id,
                media_type=media_type,
                variant=variant,
                hash_value=hash_value,
                bucket0=b0,
                bucket1=b1,
                bucket2=b2,
                bucket3=b3,
                banned=banned,
            )
            db.add(row)
        elif banned:
            row.banned = True
        saved += 1
    return saved


async def record_visual_hashes(msg: Message, bot: Bot, banned: bool = False) -> int:
    entries = media_file_entries(msg)
    if not entries:
        return 0
    user_id = msg.from_user.id if msg.from_user else None
    total = 0
    async with SessionLocal() as db:
        for entry in entries:
            fingerprints = await visual_fingerprints(bot, entry)
            total += await _save_visual_hashes(
                db,
                user_id=user_id,
                media_type=entry.visual_type or entry.media_type,
                fingerprints=fingerprints,
                banned=banned,
            )
        await db.commit()
    return total


async def contains_banned_visual(bot: Bot, msg: Message) -> bool:
    for entry in media_file_entries(msg):
        fingerprints = await visual_fingerprints(bot, entry)
        visual_type = entry.visual_type or entry.media_type
        limit = _distance_limit(visual_type)
        for _variant, hash_value in fingerprints:
            b0, b1, b2, b3 = _buckets(hash_value)
            async with SessionLocal() as db:
                result = await db.execute(
                    select(PerceptualHash.hash_value).where(
                        PerceptualHash.banned == True,
                        PerceptualHash.media_type == visual_type,
                    ).limit(10000)
                )
                candidates = result.scalars().all()
            if any(_hamming(hash_value, candidate) <= limit for candidate in candidates):
                return True
    return False


async def contains_banned_media(bot: Bot, msg: Message) -> bool:
    entries = media_file_entries(msg)
    if not entries:
        return False

    # 1. Identifiant Telegram : instantané et sans téléchargement.
    unique_ids = [entry.unique_id for entry in entries]
    async with SessionLocal() as db:
        result = await db.execute(
            select(MediaHash.id).where(
                MediaHash.file_unique_id.in_(unique_ids),
                MediaHash.banned == True,
            ).limit(1)
        )
        if result.scalar_one_or_none() is not None:
            return True

    # 2. Empreinte visuelle de la photo ou de la miniature vidéo : légère.
    if await contains_banned_visual(bot, msg):
        return True

    # 3. SHA-256 seulement pour les petits fichiers. Les longues vidéos sont
    # volontairement ignorées ici afin de ne pas ralentir le bot.
    sha_keys: list[str] = []
    for entry in entries:
        sha = await file_sha256(bot, entry.file_id, entry.file_size)
        if sha:
            sha_keys.append(sha)
    if not sha_keys:
        return False

    async with SessionLocal() as db:
        result = await db.execute(
            select(MediaHash.id).where(
                MediaHash.file_unique_id.in_(sha_keys),
                MediaHash.banned == True,
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None


async def ban_hash_from_message(msg: Message, bot: Bot | None = None) -> int:
    entries = media_file_entries(msg)
    if not entries:
        return 0

    user_id = msg.from_user.id if msg.from_user else None
    count = 0
    async with SessionLocal() as db:
        for entry in entries:
            keys = [entry.unique_id]
            if bot:
                sha = await file_sha256(bot, entry.file_id, entry.file_size)
                if sha:
                    keys.append(sha)
            for key in keys:
                result = await db.execute(select(MediaHash).where(MediaHash.file_unique_id == key))
                row = result.scalar_one_or_none()
                if row is None:
                    row = MediaHash(
                        user_id=user_id,
                        file_unique_id=key,
                        file_id=entry.file_id,
                        media_type=entry.media_type,
                        banned=True,
                    )
                    db.add(row)
                else:
                    row.banned = True
                    row.file_id = entry.file_id
                    row.media_type = entry.media_type
                count += 1

            if bot:
                fingerprints = await visual_fingerprints(bot, entry)
                count += await _save_visual_hashes(
                    db,
                    user_id=user_id,
                    media_type=entry.visual_type or entry.media_type,
                    fingerprints=fingerprints,
                    banned=True,
                )
        await db.commit()
    return count


async def ban_all_known_media_for_user(user_id: int) -> None:
    async with SessionLocal() as db:
        await db.execute(update(MediaHash).where(MediaHash.user_id == user_id).values(banned=True))
        await db.execute(update(PerceptualHash).where(PerceptualHash.user_id == user_id).values(banned=True))
        await db.commit()


async def banned_hash_count() -> int:
    async with SessionLocal() as db:
        binary_count = int(
            (await db.execute(select(func.count(MediaHash.id)).where(MediaHash.banned == True))).scalar() or 0
        )
        visual_count = int(
            (await db.execute(select(func.count(PerceptualHash.id)).where(PerceptualHash.banned == True))).scalar() or 0
        )
        return binary_count + visual_count
