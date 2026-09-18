# Seed files

Every file here is a **real image**. Frappe re-encodes uploads through PIL when
`strip_exif_metadata_from_uploaded_images` is on, so a text file with a `.jpg`
name dies inside `File` rather than at the call site.

- `proof_sample.jpg` — tiny valid 1×1 JPEG (407 bytes) attached to seeded
  CBT Payment Proof records. Small on purpose: committed to git and inserted
  on every seed run.
- `media_banner.jpg` (640×160) — `CBT Company.banner` for the `qc-smash`
  fixture; the hero across the top of its `/book` page.
- `media_court_1.jpg`, `media_court_center.jpg`, `media_courts_night.jpg`,
  `media_lounge.jpg`, `media_showers.jpg`, `media_league_night.jpg`
  (160×120) — the six seeded `CBT Company Media` rows (section-22), in three
  groups. The first two are court-scoped; the rest are general.
- `gcash_qr_sample.png` (148×148, 321 bytes, a real QR — segno, payload
  `GCASH|0917-000-2222|QC Smash|sample`) — `CBT Payment Channel.qr_image` on the
  seeded GCash rows (2026-09-04), so the checkout's QR tile and lightbox render
  from seed data. ONE public, UNATTACHED File serves both rows on purpose: an
  attached File is deleted when its field is cleared, which would 404 the other
  tenant's tile. The seed restores its blob after a snapshot_reset and leaves an
  operator's own QR alone (`_seed_gcash_qr`).

## Why every image has DISTINCT bytes

`File.validate_duplicate_entry` matches on `content_hash` **+ `is_private`**,
and adds `attached_to_doctype`/`attached_to_name` to the filter only when both
are set (`frappe/core/doctype/file/file.py:475-503`). The gallery images are
created as plain public Files, so that dedup is effectively **site-wide**: two
byte-identical seed images would silently collapse onto ONE `file_url` — the
gallery would still render, but no test could tell one photo from another, and
neither could a human looking at the demo. Each image therefore carries a
different colour and geometry. Regenerate them the same way if they are ever
replaced.

## Oversized-file manual test

No big binary is committed. To exercise the `proof_max_mb` / `media_max_mb`
rejection manually (backend tests just generate bytes in-memory), create one on
demand:

```bash
# 11 MB of zeros with a jpg extension (both limits default to 10 MB)
dd if=/dev/zero of=/tmp/too_big.jpg bs=1M count=11
```

Then upload it through the proof-upload API / portal, or attach it to a
CBT Company Media row, and expect the friendly "too large" error.
