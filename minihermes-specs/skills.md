# Mini-Hermes — Ranked Skill Catalog

Companion to `minihermes-plan-final.md` §10. Generated from the real tree at
`/workspaces/project4/Hermes-Agent` — regenerate with
`python3 minihermes-specs/gen_skills_catalog.py` rather than editing by hand.

**Everything ships. Nothing is dropped.** What the tiers change is what competes
for the model's attention on each turn.

| Tier | Meaning | Count |
|---|---|---|
| **T1** | always indexed — needed on most turns | 32 |
| **T2** | indexed on demand — useful when the task looks related | 105 |
| **T3** | archive — on disk, fetchable by name, not indexed | 76 |
| | **total** | **213** |

Portability is a separate axis:

| Portability | Count | Meaning |
|---|---|---|
| portable | 183 | pure stdlib / HTTP / filesystem |
| needs-cli | 15 | works if the named binary is installed |
| needs-dep | 10 | would need a Python package — conflicts with C1 as-is |
| not-portable | 5 | macOS-only |

Why the tier split exists: a 60-entry index measures at **0.1 MB**, so size is
not the constraint — *selection quality* is. An agent choosing among 213 skills
picks worse than one choosing among 40. Tiers give you the whole collection
without the selection penalty, and `set_tier` lets the agent promote a skill
itself when it turns out to matter.

---

## T1 — always indexed (32)

### optional-skills / software-development

| Skill | Portability | Size | Description |
|---|---|---|---|
| `rest-graphql-debug` | portable | 15534B | Debug REST/GraphQL APIs: status codes, auth, schemas, repro. |
| `subagent-driven-development` | portable | 10662B | Execute plans via delegate_task subagents (2-stage review). |

### optional-skills / web-development

| Skill | Portability | Size | Description |
|---|---|---|---|
| `har-derived-api-client` | portable | 10345B | Record a site's XHR into a HAR, derive an HTTP client. |

### skills / autonomous-ai-agents

| Skill | Portability | Size | Description |
|---|---|---|---|
| `claude-code` | needs-cli | 35034B | Delegate coding to Claude Code CLI (features, PRs). |
| `codex` | needs-cli | 5834B | Delegate coding to OpenAI Codex CLI (features, PRs). |
| `hermes-agent` | portable | 13104B | Use, configure, theme, extend, and orchestrate Hermes Agent. |
| `opencode` | needs-cli | 7289B | Delegate coding to OpenCode CLI (features, PR review). |

### skills / email

| Skill | Portability | Size | Description |
|---|---|---|---|
| `email-inbox-triage` | portable | 5435B | Triage an inbox: prioritize threads, draft replies safely. |

### skills / media

| Skill | Portability | Size | Description |
|---|---|---|---|
| `youtube-content` | portable | 4363B | YouTube transcripts to summaries, threads, blogs. |

### skills / note-taking

| Skill | Portability | Size | Description |
|---|---|---|---|
| `obsidian` | portable | 3092B | Read, search, create, and edit notes in the Obsidian vault. |

### skills / productivity

| Skill | Portability | Size | Description |
|---|---|---|---|
| `document-to-action-items` | portable | 3904B | Extract cited obligations, deadlines, tasks from documents. |
| `docx` | portable | 10379B | Create, read, edit, template, and review Word .docx files. |
| `maps` | portable | 6657B | Geocode, POIs, routes, timezones via OpenStreetMap/OSRM. |
| `meeting-action-items` | portable | 3800B | Turn meeting notes into cited decisions, owners, tickets. |
| `pdf` | portable | 12455B | PDF files: create, read, merge, fill, OCR, edit text. |
| `weekly-review-planning` | portable | 3973B | Weekly reset: commitments, stalled work, next-week plan. |
| `xlsx` | portable | 9649B | Create, read, edit Excel .xlsx workbooks and CSVs. |

### skills / research

| Skill | Portability | Size | Description |
|---|---|---|---|
| `arxiv` | portable | 10047B | Search arXiv papers by keyword, author, category, or ID. |
| `competitor-news-monitor` | portable | 4611B | Watch named companies for material news; cited digests. |
| `grounded-citations` | portable | 12586B | Ground answers and documents in cited, verifiable sources. |
| `llm-wiki` | portable | 19895B | Karpathy's LLM Wiki: build/query interlinked markdown KB. |

### skills / software-development

| Skill | Portability | Size | Description |
|---|---|---|---|
| `codebase-inspection` | needs-cli | 3584B | Inspect codebases w/ pygount: LOC, languages, ratios. |
| `dogfood` | portable | 6294B | Exploratory QA of web apps: find bugs, evidence, reports. |
| `github` | needs-cli | 2440B | GitHub via gh CLI: PRs, issues, reviews, repos, auth. |
| `hermes-agent-skill-authoring` | portable | 14453B | Author in-repo SKILL.md files: frontmatter and structure. |
| `python-debugpy` | portable | 14964B | Debug Python: pdb REPL + debugpy remote (DAP). |
| `requesting-code-review` | portable | 8741B | Pre-commit review: security scan, quality gates, auto-fix. |
| `simplify-code` | portable | 14604B | Parallel 4-agent cleanup of recent code changes. |
| `spike` | portable | 8608B | Throwaway experiments to validate an idea before build. |
| `systematic-debugging` | portable | 14015B | 4-phase root cause debugging: understand bugs before fixing. |
| `test-driven-development` | portable | 10252B | TDD: enforce RED-GREEN-REFACTOR, tests before code. |

### skills / web

| Skill | Portability | Size | Description |
|---|---|---|---|
| `blocked-page-recovery` | portable | 5139B | Use when a fetch fails: 403/429, paywall, WAF, bot wall. |

## T2 — indexed on demand (105)

### optional-skills / autonomous-ai-agents

| Skill | Portability | Size | Description |
|---|---|---|---|
| `agent-merge-conflict-arbiter` | portable | 7597B | Neutral arbiter for merge conflicts between two agents. |
| `antigravity-cli` | portable | 9463B | Operate the Antigravity CLI (agy): plugins, auth, sandbox. |
| `blackbox` | portable | 5502B | Delegate coding tasks to the Blackbox AI multi-model CLI. |
| `dynamic-workflow` | portable | 11227B | Plan-in-code fan-outs, adversarial verification, waves. |
| `grok` | portable | 13832B | Delegate coding to xAI Grok Build CLI (features, PRs). |
| `honcho` | portable | 19451B | Configure and troubleshoot Honcho memory for Hermes. |
| `openhands` | portable | 8299B | Delegate coding to OpenHands CLI (model-agnostic, LiteLLM). |

### optional-skills / creative

| Skill | Portability | Size | Description |
|---|---|---|---|
| `ai-presenter-video` | portable | 9238B | Make a verified AI presenter video from script + image. |
| `anatomy` | portable | 33685B | Explain an idea as an interactive isometric machine figure. |
| `archify` | portable | 2445B | Validated interactive HTML diagrams, upstream-maintained. |
| `ascii-art` | portable | 10446B | ASCII art: pyfiglet, cowsay, boxes, image-to-ascii. |
| `audiocraft-audio-generation` | portable | 15427B | AudioCraft: MusicGen text-to-music, AudioGen text-to-sound. |
| `auteur` | portable | 22539B | Design and build cinematic, award-level web pages. |
| `baoyu-article-illustrator` | portable | 9965B | Article illustrations: type × style × palette consistency. |
| `baoyu-comic` | portable | 14427B | Knowledge comics (知识漫画): educational, biography, tutorial. |
| `brag` | portable | 2857B | Project launch video via Hyperframes, upstream-maintained. |
| `brag-slim` | portable | 1985B | Launch video from a project or URL, upstream-maintained. |
| `comfyui` | portable | 23483B | Generate images, video, and audio via diffusion workflows. |
| `concept-diagrams` | portable | 16422B | Generate flat, minimal educational SVG visuals as HTML. |
| `creative-ideation` | portable | 10315B | Generate ideas via named methods from creative practice. |
| `draw-your-font` | portable | 6901B | Turn a handwriting photo into an installable TTF font. |
| `dream-loop` | portable | 13885B | Build stunning 3D scenes via a concept-art fidelity loop. |
| `excalidraw` | portable | 7275B | Hand-drawn Excalidraw JSON diagrams (arch, flow, seq). |
| `heartmula` | portable | 6488B | HeartMuLa: Suno-like song generation from lyrics + tags. |
| `hyperframes` | portable | 15523B | Render MP4/WebM videos from HTML compositions. |
| `impeccable` | portable | 1547B | Frontend design guidance, upstream-maintained (impeccable). |
| `ip-as-logo` | portable | 18847B | Design minimal cute IP mascot marks readable at 32px. |
| `kanban-video-orchestrator` | portable | 9693B | Plan and run multi-agent video production pipelines. |
| `meme-generation` | portable | 5553B | Create meme PNGs from templates with Pillow text overlay. |
| `mono-color` | portable | 11692B | Generate one- or two-ink editorial print poster images. |
| `pixel-art` | portable | 7603B | Pixel art w/ era palettes (NES, Game Boy, PICO-8). |
| `pretext` | portable | 13896B | Build creative browser demos with DOM-free text layout. |
| `simple-english` | portable | 20474B | Rewrite text to ASD-STE100 Simplified Technical English. |
| `sketch` | portable | 9665B | Throwaway HTML mockups: 2-3 design variants to compare. |
| `social-media-content-calendar` | portable | 3968B | Plan multi-platform social campaigns: briefs to posting. |
| `system-atlas` | portable | 8828B | Build explorable isometric architecture atlases as HTML. |
| `tldraw-offline` | portable | 14923B | Drive and script tldraw offline canvases with an agent. |
| `unreal-mcp` | portable | 13823B | Automate Unreal Engine editor scenes, actors, and renders. |

### optional-skills / devops

| Skill | Portability | Size | Description |
|---|---|---|---|
| `actual-setup` | portable | 6743B | Set up Actual Computer (actual.inc) inference in Hermes. |
| `docker-management` | portable | 10386B | Manage Docker containers, images, volumes, and Compose. |
| `hermes-s6-container-supervision` | portable | 11717B | Modify or debug s6 services in the Hermes Docker image. |
| `inference-sh-cli` | portable | 4469B | Run 150+ AI apps (image, video, LLM) via inference.sh CLI. |
| `pinggy-tunnel` | portable | 14035B | Zero-install localhost tunnels over SSH via Pinggy. |
| `setup-wizard-generator` | portable | 4949B | Generate a bash wizard guiding a human through manual setup. |
| `watchers` | portable | 4490B | Poll RSS, JSON APIs, and GitHub with watermark dedup. |

### optional-skills / email

| Skill | Portability | Size | Description |
|---|---|---|---|
| `agentmail` | portable | 3124B | Use when an agent needs AgentMail CLI email inboxes. |

### optional-skills / productivity

| Skill | Portability | Size | Description |
|---|---|---|---|
| `canvas` | portable | 3216B | Fetch Canvas LMS courses and assignments via API token. |
| `decision-questionnaire` | portable | 4161B | Turn an unanswerable decision into a questionnaire doc. |
| `first-task` | portable | 9810B | Run the first task chat that setup hands off. |
| `here-now` | portable | 8768B | Publish sites to {slug}.here.now and store files in Drives. |
| `initiate-setup` | portable | 9378B | Run the first-run setup chat in the Hermes desktop app. |
| `live-dashboard` | portable | 8304B | Build self-updating dashboards from live sources. |
| `memento-flashcards` | portable | 13566B | Spaced-repetition flashcards: create, review, quiz, export. |
| `property-listings` | portable | 4818B | Present property and rental listings as desktop cards. |
| `shop` | portable | 16615B | Shop catalog search, checkout, order tracking, returns. |
| `shopify` | portable | 15121B | Query Shopify Admin/Storefront GraphQL APIs via curl. |
| `siyuan` | portable | 9237B | Query and edit a SiYuan knowledge base via its API. |
| `telephony` | portable | 12780B | Provision Twilio numbers, SMS/MMS, and AI outbound calls. |

### optional-skills / research

| Skill | Portability | Size | Description |
|---|---|---|---|
| `bioinformatics` | portable | 13694B | Gateway to 400+ genomics and computational biology skills. |
| `blogwatcher` | portable | 6419B | Monitor blogs and RSS/Atom feeds via blogwatcher-cli tool. |
| `darwinian-evolver` | portable | 8518B | Evolve prompts/regex/SQL/code with Imbue's evolution loop. |
| `domain-intel` | portable | 4501B | Passive recon of subdomains, SSL certs, WHOIS, and DNS. |
| `drug-discovery` | portable | 8865B | Drug discovery: ChEMBL search, drug-likeness, interactions. |
| `duckduckgo-search` | portable | 8274B | Free keyless web, news, and image search via ddgs. |
| `gitnexus-explorer` | portable | 7127B | Serve an interactive codebase knowledge graph web UI. |
| `osint-investigation` | portable | 11614B | Follow the money via public records and sanctions data. |
| `parallel-cli` | portable | 10861B | Agent-native web search, deep research, and enrichment. |
| `pinecone-research` | portable | 3051B | Agent RAG and long-term memory with Pinecone. |
| `qmd` | portable | 12933B | Hybrid local search over notes, docs, and transcripts. |
| `research-paper-writing` | portable | 71322B | Write ML papers for NeurIPS/ICML/ICLR: design→submit. |
| `rss-feeds` | portable | 4533B | Read RSS, Atom, JSON feeds; discover feeds behind a page. |
| `scrapling` | portable | 8993B | Scrape sites with stealth browsing and Cloudflare bypass. |
| `searxng-search` | portable | 6738B | Free keyless meta-search aggregating 70+ engines. |

### optional-skills / software-development

| Skill | Portability | Size | Description |
|---|---|---|---|
| `ast-grep` | portable | 12984B | AST-aware structural code search and rewrite via ast-grep. |
| `code-wiki` | portable | 14035B | Generate wiki docs + Mermaid diagrams for any codebase. |
| `grill-me` | portable | 4659B | Adversarial plan interview before implementation. |
| `pr-lens` | portable | 9694B | Draw code changes as animated architecture/data-flow SVGs. |

### optional-skills / web-development

| Skill | Portability | Size | Description |
|---|---|---|---|
| `cloudflare-temporary-deploy` | needs-cli | 7433B | Deploy a Worker live, no account, via wrangler --temporary. |
| `page-agent` | needs-dep | 8486B | Embed an in-page natural-language GUI copilot in web apps. |
| `publish-site` | needs-cli | 8582B | Versioned site deploys to GitHub/Cloudflare/Netlify Pages. |
| `scrollcraft` | needs-dep | 11957B | Premium scroll-driven landing pages; scroll = timeline. |

### skills / autonomous-ai-agents

| Skill | Portability | Size | Description |
|---|---|---|---|
| `computer-use` | needs-dep | 17954B | Drive the desktop background-first; escalate on signal. |

### skills / creative

| Skill | Portability | Size | Description |
|---|---|---|---|
| `architecture-diagram` | portable | 5818B | Dark-themed SVG architecture/cloud/infra diagrams as HTML. |
| `ascii-video` | needs-dep | 14891B | ASCII video: convert video/audio to colored ASCII MP4/GIF. |
| `baoyu-infographic` | needs-dep | 10337B | Infographics: 21 layouts x 21 styles (信息图, 可视化). |
| `claude-design` | portable | 25010B | Design one-off HTML artifacts (landing, deck, prototype). |
| `design-md` | portable | 8435B | Author/validate/export Google's DESIGN.md token spec files. |
| `humanizer` | portable | 34415B | Humanize text: strip AI-isms and add real voice. |
| `manim-video` | needs-dep | 12114B | Manim CE animations: 3Blue1Brown math/algo videos. |
| `p5js` | needs-dep | 27369B | p5.js sketches: gen art, shaders, interactive, 3D. |
| `popular-web-designs` | portable | 9716B | 54 real design systems (Stripe, Linear, Vercel) as HTML/CSS. |
| `songwriting-and-ai-music` | portable | 10907B | Songwriting craft and Suno AI music prompts. |

### skills / devops

| Skill | Portability | Size | Description |
|---|---|---|---|
| `sdlc-review` | portable | 9065B | Review Kanban handoffs and route verified outcomes. |

### skills / email

| Skill | Portability | Size | Description |
|---|---|---|---|
| `himalaya` | needs-cli | 7254B | Himalaya CLI: IMAP/SMTP email from terminal. |

### skills / media

| Skill | Portability | Size | Description |
|---|---|---|---|
| `gif-search` | needs-cli | 2718B | Search/download GIFs from Tenor via curl + jq. |
| `songsee` | needs-dep | 2336B | Audio spectrograms/features (mel, chroma, MFCC) via CLI. |

### skills / productivity

| Skill | Portability | Size | Description |
|---|---|---|---|
| `airtable` | needs-cli | 11268B | Airtable REST API via curl. Records CRUD, filters, upserts. |
| `box` | needs-cli | 11693B | Box manages cloud files, sharing, search, and metadata. |
| `google-workspace` | needs-cli | 14167B | Gmail, Calendar, Drive, Docs, Sheets via gws CLI or Python. |
| `notion` | needs-cli | 14564B | Notion API + ntn CLI: pages, databases, markdown, Workers. |
| `powerpoint` | needs-cli | 10755B | Create, read, edit .pptx decks with python-pptx. |
| `product-price-monitor` | portable | 4394B | Watch product, flight, or listing prices; alert on target. |
| `teams-meeting-pipeline` | portable | 7180B | Teams meeting summaries, job replay, Graph subscriptions. |

### skills / software-development

| Skill | Portability | Size | Description |
|---|---|---|---|
| `inspecting-hermes-desktop-dom` | needs-dep | 6228B | Read the live Hermes desktop DOM/CSS over CDP. |
| `node-inspect-debugger` | needs-dep | 11045B | Debug Node.js via --inspect + Chrome DevTools Protocol CLI. |

## T3 — archive (76)

### optional-skills / (root)

| Skill | Portability | Size | Description |
|---|---|---|---|
| `yuanbao` | portable | 3723B | Yuanbao (元宝) groups: @mention users, query info/members. |

### optional-skills / blockchain

| Skill | Portability | Size | Description |
|---|---|---|---|
| `evm` | portable | 8365B | Read-only EVM client: wallets, tokens, gas across 8 chains. |
| `hyperliquid` | portable | 6909B | Hyperliquid market data, account history, trade review. |
| `solana` | portable | 6385B | Query Solana wallets, tokens, txs, and NFTs in USD. |

### optional-skills / communication

| Skill | Portability | Size | Description |
|---|---|---|---|
| `one-three-one-rule` | portable | 4573B | 1-3-1 decision briefs: problem, three options, one pick. |

### optional-skills / data-science

| Skill | Portability | Size | Description |
|---|---|---|---|
| `jupyter-notebook` | portable | 5987B | Iterative Python via live Jupyter kernel (hamelnb). |

### optional-skills / dogfood

| Skill | Portability | Size | Description |
|---|---|---|---|
| `adversarial-ux-test` | portable | 8892B | Roleplay a hostile user to find and triage UX pain points. |

### optional-skills / finance

| Skill | Portability | Size | Description |
|---|---|---|---|
| `3-statement-model` | portable | 21516B | Build integrated IS/BS/CF financial workbooks in Excel. |
| `comps-analysis` | portable | 29560B | Build comparable-company valuation workbooks in Excel. |
| `dcf-model` | portable | 49020B | Build discounted cash flow valuation workbooks in Excel. |
| `excel-author` | portable | 8782B | Build auditable financial workbooks headless via openpyxl. |
| `lbo-model` | portable | 16838B | Build leveraged buyout workbooks with IRR/MOIC in Excel. |
| `merger-model` | portable | 5287B | Build M&A accretion/dilution workbooks in Excel. |
| `polymarket` | portable | 2980B | Query Polymarket: markets, prices, orderbooks, history. |
| `pptx-author` | portable | 6396B | Build PowerPoint decks headless with python-pptx. |
| `stocks` | portable | 2739B | Stock quotes, history, search, compare, crypto via Yahoo. |

### optional-skills / gaming

| Skill | Portability | Size | Description |
|---|---|---|---|
| `minecraft-modpack-server` | portable | 6608B | Host modded Minecraft servers (CurseForge, Modrinth). |
| `pokemon-player` | portable | 8792B | Play Pokemon via headless emulator + RAM reads. |

### optional-skills / health

| Skill | Portability | Size | Description |
|---|---|---|---|
| `fitness-nutrition` | portable | 9783B | Workout planning, macros, and body metrics via wger/USDA. |
| `neuroskill-bci` | portable | 16282B | Use live BCI cognitive and mood state from NeuroSkill. |

### optional-skills / mcp

| Skill | Portability | Size | Description |
|---|---|---|---|
| `fastmcp` | portable | 8152B | Build, test, and deploy Python MCP servers. |
| `mcp-oauth-remote-gateway` | portable | 23883B | Manual OAuth for remote MCP servers on headless gateways. |
| `mcporter` | portable | 3076B | List, auth, and call MCP servers/tools from the terminal. |

### optional-skills / migration

| Skill | Portability | Size | Description |
|---|---|---|---|
| `openclaw-migration` | portable | 15943B | Import an OpenClaw setup (memories, skills) into Hermes. |

### optional-skills / mlops

| Skill | Portability | Size | Description |
|---|---|---|---|
| `accelerate` | portable | 9167B | Run PyTorch training across GPUs with minimal changes. |
| `chroma` | portable | 8943B | Embedding database for RAG and semantic search. |
| `clip` | portable | 6687B | Zero-shot image classification and image-text search. |
| `faiss` | portable | 4813B | Fast vector similarity search at billion scale. |
| `flash-attention` | portable | 10634B | Speed up long-sequence transformer training and inference. |
| `guidance` | portable | 15196B | Constrain LLM output with grammars; guarantee valid JSON. |
| `huggingface-tokenizers` | portable | 13411B | Fast BPE/WordPiece tokenization and custom vocab training. |
| `instructor` | portable | 16650B | Structured LLM outputs validated with Pydantic. |
| `lambda-labs` | portable | 11981B | On-demand GPU cloud instances for ML training. |
| `llava` | portable | 7590B | Vision-language chat: VQA, captioning, image dialogue. |
| `modal` | portable | 9013B | Serverless GPU cloud for ML jobs and model APIs. |
| `nemo-curator` | portable | 10367B | Curate LLM training data: dedupe, filter, PII redaction. |
| `obliteratus` | portable | 15386B | OBLITERATUS: abliterate LLM refusals (diff-in-means). |
| `peft` | portable | 11997B | Fine-tune large LLMs with LoRA on limited GPU memory. |
| `pinecone` | portable | 8524B | Managed vector DB for production RAG and search. |
| `pytorch-fsdp` | portable | 2474B | Fully sharded data-parallel training for large models. |
| `pytorch-lightning` | portable | 8966B | Clean training loops with built-in distributed support. |
| `qdrant` | portable | 13773B | Vector search engine for production RAG systems. |
| `saelens` | portable | 14974B | Train sparse autoencoders to interpret model features. |
| `simpo` | portable | 5748B | Reference-free preference alignment, simpler than DPO. |
| `slime` | portable | 10937B | RL post-training for LLMs with Megatron and SGLang. |
| `stable-diffusion` | portable | 12782B | Text-to-image generation, inpainting, and img2img. |
| `tensorrt-llm` | portable | 5042B | High-throughput LLM inference on NVIDIA GPUs. |
| `torchtitan` | portable | 10342B | Pretrain LLMs at scale with PyTorch 4D parallelism. |
| `whisper` | portable | 7212B | Transcribe and translate speech in 99 languages. |

### optional-skills / mlops/evaluation

| Skill | Portability | Size | Description |
|---|---|---|---|
| `evaluating-llms-harness` | portable | 12212B | lm-eval-harness: benchmark LLMs (MMLU, GSM8K, etc.). |
| `weights-and-biases` | portable | 12672B | W&B: log ML experiments, sweeps, model registry, dashboards. |

### optional-skills / mlops/inference

| Skill | Portability | Size | Description |
|---|---|---|---|
| `llama-cpp` | portable | 8870B | llama.cpp local GGUF inference + HF Hub model discovery. |
| `outlines` | portable | 17247B | Outlines: structured JSON/regex/Pydantic LLM generation. |
| `serving-llms-vllm` | portable | 9335B | vLLM: high-throughput LLM serving, OpenAI API, quantization. |

### optional-skills / mlops/models

| Skill | Portability | Size | Description |
|---|---|---|---|
| `huggingface-hub` | portable | 3709B | HuggingFace hf CLI: search/download/upload models, datasets. |
| `segment-anything-model` | portable | 13079B | SAM: zero-shot image segmentation via points, boxes, masks. |

### optional-skills / mlops/research

| Skill | Portability | Size | Description |
|---|---|---|---|
| `dspy` | portable | 15212B | DSPy: declarative LM programs, auto-optimize prompts, RAG. |

### optional-skills / mlops/training

| Skill | Portability | Size | Description |
|---|---|---|---|
| `axolotl` | portable | 4895B | Axolotl: YAML LLM fine-tuning (LoRA, DPO, GRPO). |
| `trl-fine-tuning` | portable | 13493B | TRL: SFT, DPO, GRPO, RLOO reward modeling for LLM RLHF. |
| `unsloth` | portable | 2275B | Unsloth: 2-5x faster LoRA/QLoRA fine-tuning, less VRAM. |

### optional-skills / payments

| Skill | Portability | Size | Description |
|---|---|---|---|
| `mpp-agent` | portable | 5041B | Pay HTTP 402 APIs via Machine Payments Protocol (MPP). |
| `stripe-link-cli` | portable | 6762B | Agent payments via Stripe Link — cards, SPT, approvals. |
| `stripe-projects` | portable | 4515B | Provision SaaS services + sync creds via Stripe Projects. |

### optional-skills / security

| Skill | Portability | Size | Description |
|---|---|---|---|
| `1password` | portable | 4678B | Set up op CLI, sign in, and read or inject secrets. |
| `godmode` | portable | 20073B | Jailbreak LLMs: Parseltongue, GODMODE, ULTRAPLINIAN. |
| `oss-forensics` | portable | 19802B | GitHub supply-chain forensics: recovery, IOCs, reporting. |
| `sherlock` | portable | 5991B | Find accounts for a username across 400+ platforms. |
| `unbroker` | portable | 25233B | Autonomously remove your info from data-broker sites. |
| `web-pentest` | portable | 12996B | Authorized web pentest: recon, proof-based exploits, report. |

### optional-skills / smart-home

| Skill | Portability | Size | Description |
|---|---|---|---|
| `openhue` | not-portable | 2877B | Control Philips Hue lights, scenes, rooms via OpenHue CLI. |

### optional-skills / social-media

| Skill | Portability | Size | Description |
|---|---|---|---|
| `reddit-reading` | portable | 6231B | Read Reddit: subreddits, search, threads, users. No browser. |

### skills / apple

| Skill | Portability | Size | Description |
|---|---|---|---|
| `apple-notes` | not-portable | 2380B | Manage Apple Notes via memo CLI: create, search, edit. |
| `apple-reminders` | not-portable | 3598B | Apple Reminders via remindctl: add, list, complete. |
| `findmy` | not-portable | 3811B | Track Apple devices/AirTags via FindMy.app on macOS. |
| `imessage` | not-portable | 2424B | Send and receive iMessages/SMS via the imsg CLI on macOS. |

### skills / social-media

| Skill | Portability | Size | Description |
|---|---|---|---|
| `xurl` | needs-cli | 16293B | X/Twitter via xurl CLI: raw post search, posting, DM, media. |

---

## Promotion and demotion

Tier is a frontmatter field. Changing it is a one-line edit:

```yaml
---
name: some-skill
description: ...
tier: 1
triggers: [keyword, keyword]
---
```

The agent can do this itself via `skill_manage(action="set_tier")`, so the
ranking improves from use rather than staying a fixed guess.

## Rules used by the generator

1. An explicit T1 list (methodology, notes, research, web, core dev tooling,
   document formats, planning, agent orchestration).
2. Any other skill whose category is in the broadly-useful set becomes T2.
3. Everything else is T3.
4. Portability is orthogonal: macOS-only categories are `not-portable`,
   skills needing a Python package are `needs-dep`, skills needing a named
   binary are `needs-cli`.

## The `needs-dep` list, explicitly

These are the only skills that cannot run under C1 without either a rewrite or
an accepted exception. Each is a decision, not an oversight:

- `ascii-video` — ASCII video: convert video/audio to colored ASCII MP4/GIF.
- `baoyu-infographic` — Infographics: 21 layouts x 21 styles (信息图, 可视化).
- `computer-use` — Drive the desktop background-first; escalate on signal.
- `inspecting-hermes-desktop-dom` — Read the live Hermes desktop DOM/CSS over CDP.
- `manim-video` — Manim CE animations: 3Blue1Brown math/algo videos.
- `node-inspect-debugger` — Debug Node.js via --inspect + Chrome DevTools Protocol CLI.
- `p5js` — p5.js sketches: gen art, shaders, interactive, 3D.
- `page-agent` — Embed an in-page natural-language GUI copilot in web apps.
- `scrollcraft` — Premium scroll-driven landing pages; scroll = timeline.
- `songsee` — Audio spectrograms/features (mel, chroma, MFCC) via CLI.

