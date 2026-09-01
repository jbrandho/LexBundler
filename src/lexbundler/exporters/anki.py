"""Low-level genanki adapter for the LexBundler Listening v1 note model."""

from dataclasses import dataclass
from pathlib import Path

import genanki

# Fixed forever for the field/template contract named "LexBundler Listening v1".
LISTENING_MODEL_ID = 1_778_120_801
LISTENING_MODEL_NAME = "LexBundler Listening v1"
LISTENING_CARD_VERSION = "listening-v1"

LISTENING_FIELDS = (
    "Audio",
    "ChineseSC",
    "Pinyin",
    "English",
    "Source",
    "LexBundlerID",
)

LISTENING_FRONT = "{{Audio}}"
LISTENING_BACK = """{{FrontSide}}
<hr id=answer>
<div class="chinese-sc">{{ChineseSC}}</div>
<div class="support">
  <div class="pinyin">{{Pinyin}}</div>
  <div class="english">{{English}}</div>
</div>
<div class="source">{{Source}}</div>"""

LISTENING_CSS = """.card {
  font-family: Arial, sans-serif;
  text-align: center;
  color: #222;
  background: #fff;
}
.chinese-sc {
  margin-top: 1.25em;
  font-family: "Kaiti SC", "STKaiti", "KaiTi", serif;
  font-size: 32px;
  line-height: 1.45;
  text-align: center;
}
.support {
  margin-top: 4.5em;
  font-family: Arial, sans-serif;
  font-size: 12px;
  line-height: 1.6;
}
.pinyin:empty, .english:empty, .source:empty {
  display: none;
}
.source {
  margin-top: 3em;
  font-family: Arial, sans-serif;
  font-size: 10px;
  color: #777;
}
"""


@dataclass(frozen=True, slots=True)
class AnkiNote:
    """One normalized, HTML-safe note ready for genanki."""

    guid: str
    audio: str
    chinese_sc: str
    pinyin: str
    english: str
    source: str
    lexbundler_id: str
    tags: tuple[str, ...]


def listening_note_guid(project_uuid: object, segment_id: int) -> str:
    """Return genanki's stable Anki GUID for one logical listening task."""
    return genanki.guid_for(project_uuid, segment_id, LISTENING_CARD_VERSION)


def write_listening_package(
    *,
    deck_id: int,
    deck_name: str,
    notes: tuple[AnkiNote, ...],
    media_paths: tuple[Path, ...],
    output_path: Path,
) -> None:
    """Write normalized listening notes and media to a staged APKG path."""
    model = genanki.Model(
        LISTENING_MODEL_ID,
        LISTENING_MODEL_NAME,
        fields=[{"name": name} for name in LISTENING_FIELDS],
        templates=[
            {
                "name": "Listening Card 1",
                "qfmt": LISTENING_FRONT,
                "afmt": LISTENING_BACK,
            }
        ],
        css=LISTENING_CSS,
    )
    deck = genanki.Deck(deck_id, deck_name)
    for item in notes:
        deck.add_note(
            genanki.Note(
                model=model,
                fields=[
                    item.audio,
                    item.chinese_sc,
                    item.pinyin,
                    item.english,
                    item.source,
                    item.lexbundler_id,
                ],
                tags=list(item.tags),
                guid=item.guid,
            )
        )
    package = genanki.Package(deck)
    package.media_files = [str(path) for path in media_paths]
    package.write_to_file(str(output_path))
