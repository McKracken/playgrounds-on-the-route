"""Evidence persistence (spec Feature 6: FR-6.1, FR-6.2, FR-6.3).

Only writes anything for a positive GMaps-photos `Decision`
(`decision_engine.decide_from_photos`'s qualifying photos held in memory
until the final label is known); an OSM-sourced or negative decision writes
nothing at all.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from playground_check.decision_engine import Decision
from playground_check.models import ResolvedPOI

#: Maps a `Photo.mime_type` to the file extension used for its saved evidence
#: file. Falls back to `.bin` for an unrecognized MIME type rather than
#: raising -- a write failure is the only sanctioned failure mode here
#: (FR-6.3), not an unrecognized-type error.
_MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

#: Any character outside this set is replaced with `-` when building the
#: output-directory slug (spec FR-6.1).
_SLUG_INVALID_CHARS = re.compile(r"[^a-z0-9-]")


def _slugify(poi: ResolvedPOI) -> str:
    raw = poi.name if poi.name else f"{poi.lat}_{poi.lng}"
    return _SLUG_INVALID_CHARS.sub("-", raw.lower())


def _extension_for(mime_type: str) -> str:
    return _MIME_TO_EXT.get(mime_type, ".bin")


def save_evidence(decision: Decision, poi: ResolvedPOI, output_dir: Path) -> list[str]:
    """Write `decision.qualifying` photos + JSON metadata sidecars to a
    per-run directory under `output_dir` (spec FR-6.1).

    Does nothing -- returns `[]` immediately, without creating any directory
    -- for an OSM-sourced decision or a negative label (spec FR-6.2), which
    covers both the OSM-hit case and the GMaps-negative case in one check.

    A failure writing an individual photo or its sidecar is logged to stderr
    and that one item is simply omitted from the returned list (spec
    FR-6.3); it never raises and never affects any other item. The returned
    list preserves `decision.qualifying`'s order.
    """
    if decision.method_used != "gmaps_photos" or decision.label != "playground nearby":
        return []

    # One shared timestamp for the whole call -- computed once here, not
    # per-photo -- used both for the per-run directory name and every
    # sidecar's `timestamp` field.
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_dir / f"{_slugify(poi)}-{timestamp}"

    written: list[str] = []
    for index, (photo, result) in enumerate(decision.qualifying):
        basename = f"photo-{index:03d}"
        photo_path = run_dir / f"{basename}{_extension_for(photo.mime_type)}"
        sidecar_path = run_dir / f"{basename}.json"
        metadata = {
            "lat": poi.lat,
            "lng": poi.lng,
            "source_url": photo.source_url,
            "timestamp": timestamp,
            "confidence": result.confidence,
        }

        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            photo_path.write_bytes(photo.bytes)
            sidecar_path.write_text(json.dumps(metadata, indent=2))
        except OSError as exc:
            print(
                f"save_evidence: failed to write evidence photo {index} "
                f"(source_url={photo.source_url!r}): {exc}",
                file=sys.stderr,
            )
            continue

        written.append(str(photo_path))

    return written
