# .macroscope/ignore.md — review scope for the speedbay/open-swe fork.
#
# Docs checked: 2026-07-28.
# - Ignore file, REPLACE semantics, and the published default patterns:
#   https://docs.macroscope.com/bug-detection-and-fixes#default-ignore-patterns
# - Config layout: https://docs.macroscope.com/check-run-agents
#
# REPLACE, not additive: committing this file turns OFF Macroscope's built-in
# defaults (they apply only when no ignore file exists). Per the docs, the
# default patterns must be copied in to be preserved — the base-pattern block
# below is the docs' "Default base patterns" accordion, mirrored from the
# Speed Bay warehouse monorepo's vetted copy. The docs' "Default test file
# patterns" accordion is deliberately OMITTED so first-party test source stays
# reviewable (a weakened or disabled test must be catchable in review).
#
# FORK SCOPE: this repo is a fork of langchain-ai/open-swe. Review effort
# focuses on the Speed Bay org layer (speedbay/, agent/middleware/speedbay_*,
# agent/utils/speedbay_*, agent/integrations/docker_local.py) and the marked
# deviations in upstream-owned files (FORK.md § Upstream deviations) — so
# upstream files such as agent/**, tests/**, and scripts/** stay IN scope,
# while upstream bulk we never author (ui/, generated openwiki/) is excluded
# below.
#
# Glob syntax (per docs): `**` matches across directories, `*` within a single
# path segment, `?` a single character; a pattern without `/` matches at any
# depth. Maximum 1,000 patterns. Matching is deterministic with no override.

# ============================================================================
# Macroscope default base patterns — "Default base patterns (vendored,
# generated, binary, etc.)" from the docs accordion.
# ============================================================================

# === Vendored / dependency directories ===
**/.git/**
**/__pycache__/**
**/.pytest_cache/**
**/.mypy_cache/**
**/.ruff_cache/**
**/venv/**
**/.venv/**
**/node_modules/**
**/site-packages/**
**/.pnpm-store/**
**/__Snapshots__/**
**/__snapshots__/**
**/.claude/skills/**
**/bower_components/**
**/jspm_packages/**
**/.next/**
**/.svelte-kit/**
**/vendor/**
**/_vendor/**
**/third_party/**
**/Pods/**
**/.bundle/**

# === Root-anchored ambiguous directories ===
build/**
env/**
ENV/**

# === Generated / build-output directories (match anywhere) ===
**/target/**
**/generated/**
**/intermediates/**
**/generated_sources/**
**/generated-sources/**
**/generated-src/**
**/src/main/generated/**

# === Minified build output ===
**/*.min.js
**/*.min.css

# === Generated protobuf / codegen files ===
**/*_pb.d.ts
**/*_pb.js
**/*.pb.go
**/*_pb2.py
**/*_pb2_grpc.py
**/*_pb2.pyi
**/*.grpc.swift
**/*.pb.swift
**/*.sql.go
**/*.designer.cs
**/*.g.dart
**/*.pb.dart
**/*_pb.rb

# === Package manager files ===
# FORK DIVERGENCE (mirrors warehouse): dependency/build manifests stay
# reviewable so supply-chain changes are never invisible — the default
# exclusions for **/package.json, **/pyproject-style manifests, etc. are
# intentionally NOT applied. Generated lock files remain excluded below.
**/*.pbxproj
**/*.xcstrings
**/*.strings
**/bun.lock

# === Lock / sum files ===
**/go.sum
**/package-lock.json
**/pnpm-lock.yaml
**/yarn.lock
**/Package.resolved
uv.lock

# === Images ===
**/*.jpg
**/*.jpeg
**/*.png
**/*.gif
**/*.svg
**/*.ico
**/*.webp
**/*.bmp
**/*.tiff

# === Fonts ===
**/*.woff
**/*.woff2
**/*.ttf
**/*.eot
**/*.otf

# === Media ===
**/*.mp3
**/*.mp4
**/*.wav
**/*.avi
**/*.mov
**/*.mkv
**/*.flac
**/*.ogg
**/*.srt

# === Archives ===
**/*.zip
**/*.tar
**/*.gz
**/*.rar
**/*.7z
**/*.bz2

# === Documents ===
**/*.pdf
**/*.doc
**/*.docx
**/*.xls
**/*.xlsx
**/*.ppt
**/*.pptx

# === Data / serialized ===
**/*.db
**/*.sqlite
**/*.sqlite3
**/*.parquet
**/*.avro
**/*.arrow
**/*.npy
**/*.pkl
**/*.jsonl

# === ML models ===
**/*.onnx
**/*.tflite
**/*.h5
**/*.safetensors

# === Compiled / binary ===
**/*.exe
**/*.dll
**/*.so
**/*.dylib
**/*.bin
**/*.pyc
**/*.class
**/*.o
**/*.a
**/*.wasm

# === Certificates / keys ===
**/*.cer
**/*.pem
**/*.p12

# === Platform-specific / non-reviewable ===
**/*.stringsdict
**/*.snap
**/*.adoc
**/*.arb
**/*.lock
**/*.po
**/*.fbx
**/*.log
**/*.xib
**/*.meta
**/*.kml
**/*.prefab
**/*.eml
**/*.csv
**/*.grpc.reflection
**/*.js.map

# ============================================================================
# Fork-specific additions — upstream bulk the org layer never authors.
# ============================================================================

# Upstream web dashboard: 185 files we never edit; deviations to it are not
# permitted by FORK.md's file placement rule. Re-scope if we ever fork the UI.
ui/**

# Generated wiki output (carries .last-update.json; regenerated, not authored).
openwiki/**

# Macroscope's own local review worktrees (never in a PR, belt-and-braces).
.worktrees/**
