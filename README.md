# Modern Reformation Sync

Generate e-reader friendly bilingual HTML and RSS feeds for Modern Reformation, then optionally
push the rendered articles to a self-hosted Readeck instance.

The project intentionally keeps secrets out of the repository. Configure API keys, Readeck
endpoint, and public Pages URL with environment variables, `.env`, or a private config file.

## Quick Start

```bash
uv sync
uv run mr-sync --config config.example.yml run
```

The default pipeline:

1. Fetches recent published resources from Modern Reformation's public Sanity API.
2. Renders Sanity Portable Text to clean article HTML.
3. Optionally translates title and body with an OpenAI-compatible API.
4. Writes static HTML plus `feed.xml` and `feed.zh.xml`.
5. Optionally pushes bilingual HTML to Readeck and prunes older synced bookmarks.

When `.mr-sync/state.json` already exists, the fetch step also asks Sanity for articles newer
than the last stored publication date and refreshes previously stored slugs. This keeps older
generated pages in the local RSS/site instead of shrinking every run back to the latest window.
Set `source.include_state_articles: false` for a stateless latest-only run.

## Environment

For local debugging, copy `.env.example` to `.env`. The CLI loads `.env` by default before
expanding `${ENV_VAR}` placeholders in YAML, while already exported shell variables win over
file values. YAML remains the structured configuration; `.env` is only for local secrets and
quick switches.

```bash
export OPENAI_API_KEY="..."
export OPENAI_BASE_URL="https://openai-compatible.example/v1"
export READECK_BASE_URL="https://readeck.example.org"
export READECK_TOKEN="..."
export SITE_BASE_URL="https://user.github.io/repo"
export TRANSLATION_ENABLED="true"
export READECK_ENABLED="false"
```

## Commands

```bash
uv run mr-sync --config config.example.yml fetch
uv run mr-sync --config config.example.yml build
uv run mr-sync --config config.example.yml push-readeck
uv run mr-sync --config config.example.yml run
uv run mr-sync --config config.example.yml live-test --limit 3 --debug-label mr-debug --cleanup
```

Use `--env-file path/to/file` if you want a separate debug environment file.

## Translation

Translation is OpenAI-compatible and tuned for Modern Reformation articles: it preserves HTML,
scripture references, names, dates, URLs, and citation markers, while translating visible prose
into faithful e-reader friendly Chinese. Direct Bible quotations can use an OpenAI-compatible
`lookup_bible` tool backed by the eBible Chinese Union Version with New Punctuation, Shen Edition
USFX archive, so the model can query exact Scripture wording instead of paraphrasing from memory.
Inspired by Read Frog, body chunks are sent in batch requests using a standalone separator, with
result-count validation, retry, and fallback to individual chunk translation. Visible reasoning
artifacts such as `<thought>...</thought>` and markdown fences are stripped before caching or
rendering, so models that expose their scratchpad do not pollute RSS, Readeck, or EPUB output.

The rendered bilingual body places the full Chinese translation first, then the full English
original in a single trailing `Original` section. Header and body images remain with the Chinese
front section only, so e-reader exports do not repeat images in the English section.

Useful knobs in `translation`:

- `request_interval_seconds` and `rpm` throttle free or rate-limited APIs.
- `chunk_chars`, `batch_enabled`, `max_batch_items`, and `max_batch_chars` control request size.
  The example config is tuned for `gemma-4-31b-it`'s large context, using bigger chunks and
  batches to reduce GitHub Actions runtime under request-rate limits.
- `max_completion_tokens` raises the output ceiling for large HTML translation batches.
- `max_retries` and `base_retry_delay_seconds` retry timeouts, network failures, 408, 409, 429,
  and 5xx responses.
- `max_requests_per_run: 0` means no request budget cap.
- `budget_exceeded: "fail"` stops loudly; `"keep_original"` fills the remaining articles with
  original HTML.

Useful knobs in `bible`:

- `enabled` controls whether translation requests expose the `lookup_bible` tool.
- `usfx_zip_path` points to `cmn-cu89s_usfx.zip`. The GitHub Action downloads it into
  `.mr-sync/cache/` before translation; local runs can use the same path.

## Readeck Images

`readeck.image_mode` defaults to `multipart`. In this mode the rendered article HTML is uploaded
as a `resource` part with `Location` set to the article URL, and downloaded article images are
attached as additional `resource` parts with their original `Location` and `Content-Type`. This
gives Readeck the best chance to archive images into exports such as EPUB. Set
`image_mode: "remote"` to send JSON HTML with remote image URLs only.

Image downloads are bounded by `max_image_count`, `max_image_bytes`, and
`max_total_image_bytes`; failed image downloads are logged and do not stop article upload. By
default only `cdn.sanity.io` images are downloaded; add hosts to `allowed_image_hosts` only when
they are trusted.

Existing synced bookmarks are controlled by `existing_policy`. The default `replace` recreates
owned Modern Reformation bookmarks so HTML, images, and EPUB exports can reflect renderer or
translation changes. `patch_metadata` preserves the old article body and only updates labels and
metadata; `skip` leaves existing bookmarks untouched. Automatic prune only removes bookmarks
that have both configured sync labels and a Modern Reformation resource URL.

## GitHub Actions

Copy `.github/workflows/sync.yml` and set these repository secrets or variables:

- `OPENAI_API_KEY`
- `READECK_TOKEN` if Readeck push is enabled
- `READECK_BASE_URL`
- `SITE_BASE_URL`

Use repository variables for non-secret values when possible.
