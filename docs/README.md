# Docs Overview

## AI-first entrypoints
- `docs/ai/knowledge.json`
- `docs/ai/chapters_index.json`
- `docs/ai/chapters/chapter_01.txt` ... `chapter_07.txt`

## Original source layer
- `docs/ref/` contains original PDF/TXT and legacy merged files.
- Keep `docs/ref` as source-of-truth archive.

## Agent
- Custom agent config: `.github/agents/tan-gai.agent.md`
- Agent now prioritizes `docs/ai/*` and falls back to `docs/ref/*`.
