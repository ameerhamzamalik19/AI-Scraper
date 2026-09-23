# ContentProcessor: full walkthrough

This file defines one central class, `ContentProcessor`, whose job is to turn raw HTML into a cleaned, structured, page-level content model. It is not the final chunking or embedding stage. It is the extraction and normalization stage that sits immediately before the rest of the pipeline.

In plain English: this file takes a page’s HTML, strips junk, identifies the page type, extracts the meaningful sections, and returns a dictionary that downstream code can use as the source for chunking, indexing, and retrieval.

---

## 1) What this file is responsible for

At a high level, the class does the following:

- Parses the page as a BeautifulSoup DOM tree
- Detects a likely SPA shell before real extraction
- Extracts structured metadata such as JSON-LD, SVG text, and `data-*` attributes
- Removes non-content elements like script/style/noscript/iframe
- Classifies the page as one of a few shapes such as `article`, `listing`, `detail`, or `howto`
- Extracts page sections, tables, lists, cards, and other structured bits
- Deduplicates repeated content
- Returns one normalized dictionary with `main_content`, `metadata`, and `document_structure`

This is intentionally a DOM-first extraction layer. It does not try to use external extraction libraries like Readability or Trafilatura. It relies on the actual DOM structure and heuristic checks.

---

## 2) File structure and logic groups

The file is organized into a few phases:

1. Utility layer
   - debug logging
   - whitespace normalization
   - dedupe helpers
   - heading checks

2. Page classification layer
   - card counting
   - product-detail detection
   - page type inference

3. Structured data extraction
   - JSON-LD parsing and formatting
   - SVG and `data-*` extraction

4. Structured field extraction
   - tables
   - lists
   - cards

5. Media chunk creation
   - image description chunk generation

6. DOM extraction pass
   - content root detection
   - section extraction from article pages
   - heading tracking
   - deduplicated flush logic

7. Final result assembly
   - metadata collection
   - main content/text assembly
   - final `process_html()` output

---

## 3) The entry point: `process_html()`

This is the main function that runs for each page.

### Step-by-step flow

### A. Start debug logging

At the top of `process_html()`:

- It opens the debug log file at `/app/debug_content_processing.log`
- It writes the start timestamp, URL, and HTML length
- Then it calls `_debug_log()` with metadata about the run

This is a useful debug hook, but it is a portability problem on local machines and Windows-based environments because the log path is hardcoded to `/app/...`.

### B. Empty HTML guard

If the HTML string is empty:

- it logs an error
- it returns `_empty_result()`

That gives a consistent empty result structure rather than crashing.

### C. Parse the DOM

Then it does:

`BeautifulSoup(html, 'html.parser')`

This is the canonical DOM the rest of the file operates on.

### D. SPA shell detection

Before removing scripts, it calls `_detect_spa_shell(soup)`.

This is meant to catch pages that are basically a browser shell without actual rendered content, such as:

- empty `#root`, `#app`, `#__next`, `#__nuxt` containers
- React hydration pages with `data-reactroot`
- very thin bodies plus many scripts

If that is detected, execution stops early and returns a structured empty result with page type `spa_shell`.

This is important because many modern sites render almost everything client-side and an initial HTML shell can look empty to a server-side scraper.

### E. Extract structured metadata before stripping scripts

The class collects three kinds of data before removing script/style tags:

- `json_ld_text` via `_extract_json_ld(soup)`
- `svg_text` via `_extract_svg_text(soup)`
- `data_attrs` via `_extract_data_attributes(soup)`

Then it combines these into `structured_text` with labels such as:

- `Structured Data (JSON-LD): ...`
- `SVG Content: ...`
- `Data Attributes: ...`

This is not a final extracted article body; it is a supplement that may carry highly useful facts such as product metadata, FAQ facts, product schema, or inline data attributes.

### F. Strip obvious non-content tags

It removes:

- `script`
- `style`
- `noscript`
- `iframe`

The code intentionally keeps SVG content for later extraction, but removes script/style blocks because they are not visible article text.

### G. Resolve title and page type

It then calls:

- `_resolve_title(soup, page_title, source_url)`
- `_classify_page_type(source_url or '', soup)`

The title resolution logic looks for:

1. an explicit `page_title` argument
2. the HTML `<title>` tag
3. the first `<h1>`
4. `meta property="og:title"`
5. fallback to the URL or `Untitled Page`

The page type classification is the first real “page-shape” decision. It tries to decide whether the page looks like:

- `listing`
- `detail`
- `howto`
- `article`

The logic favors DOM signals first, then URL signals.

### H. Extract section structure

The class creates empty arrays for:

- `sections`
- `structured_tables`
- `structured_lists`
- `structured_cards`

Then it calls:

`dom_sections = cls._extract_full_dom(soup, source_url or '', title)`

After that, it branch-handles by page type:

- If `listing`: it merges normal DOM sections with card-specific extraction and structured card extraction
- If `howto`: it uses a special how-to extraction path and merges with DOM sections
- Else: it keeps normal DOM sections only

The merge function is `_merge_sections()`, which deduplicates exact repeated section content while preserving the first occurrence.

### I. Secondary structured extraction

After the page-type-specific extraction, the class fills in structured extras only if they weren’t already populated:

- `structured_tables = _extract_tables_structured(soup)`
- `structured_lists = _extract_lists_structured(soup)`
- `structured_cards = _extract_cards_structured(soup)`

This means the functions are designed to capture table/list/card content separately from generic article sections.

### J. Add meta description as a section

If the page has a meta description and it is longer than 60 chars and is not already present verbatim, it appends a synthetic section:

- heading: `Page Description`
- content: the meta description

This helps keep the page description as a meaningful retrieval artifact even if the main body was empty or sparse.

### K. Compute `has_content`

The page is considered to have content if any of the following are non-empty:

- `sections`
- `structured_tables`
- `structured_lists`
- `structured_cards`

### L. Build a plain-text body from sections

The code joins all section contents into `sections_text` by stripping the prefix like `[Title > Heading]` and keeping only the actual section text.

This is the general article text the system uses as the main content payload.

Then it prepends `structured_text` if present:

`all_text = f"{structured_text}\n\n{sections_text}"`

This means JSON-LD and SVG metadata are deliberately mixed into the final all-text output, which is useful for retrieval but could also duplicate information already in ordinary page sections.

### M. Assemble final dictionary

The final result is a single dictionary with keys such as:

- `page_title`
- `source_url`
- `page_type`
- `main_content`
- `metadata`
- `document_structure`
- `text_stats`

This is the object downstream code receives after HTML parsing and page normalization.

---

## 4) Utility methods and how they behave

### `_debug_log()`

This writes a timestamped log entry to the configured debug path, including JSON for dictionaries/lists. It is mostly a diagnostics hook.

### `_cell_text(cell)`

This extracts text from a table cell while preserving whitespace separation between nested elements. The docstring calls out a specific bug pattern it tries to fix:

- "23Rank3,467Mentions" becomes "23 Rank 3,467 Mentions"

This matters because chunkers often need clean separation between numbers and labels.

### `_is_junk(text)`

This checks if a text blob is effectively empty or boilerplate-only.

It flags strings that are:

- empty
- extremely short
- comprised only of punctuation / decorative symbols

This helps avoid injecting meaningless content fragments as sections.

### `_normalize_text(text)`

Returns lowercase, whitespace-normalized text for deduplication. This is a simple canonicalization step for hashing and section dedupe.

### `_section_key(section)`

Returns a normalized content hash key used to dedupe sections.

### `_merge_sections(*section_groups)`

This merges multiple lists of section dictionaries while preserving first occurrences and skipping exact duplicates. This is important when a page yields both generic DOM sections and more specialized extraction data.

### `_looks_like_heading(text)`

This rejects heading-like text that is likely not a true section heading, such as:

- dates
- "Last updated"
- CTA text like "Learn more"
- "Sign up"
- "Follow us"
- "Posted on"

This is useful because HTML headings are often overloaded with boilerplate and metadata.

### `_nearest_heading(element)`

Walks backwards through previous heading tags (`h1`-`h6`) to find the nearest meaningful heading. This is used for tables and lists so the output can carry the table/list heading context.

---

## 5) Page classification logic

The class tries to classify the page before deciding how to extract it.

### `_count_cards(soup)`

This counts repeated card-like containers by looking at:

- `data-product`
- `data-item`
- `data-model`
- `.product-card`
- `.item-card`
- `.grid-item`
- and repeated class names that look like product/listing wrappers

If the page has many repeated containers, it likely indicates a listing page.

### `_has_product_detail_signals(soup)`

This checks for signals of a product detail page:

- `[itemprop="price"]`
- product detail classes like `product-detail` or `pdp`
- `#product-detail`
- `[data-product-id]`
- JSON-LD with `@type: Product`

This is important because generic article logic is often wrong for catalog pages.

### `_classify_page_type(url, soup)`

The classification order is:

1. DOM shape
2. URL-based pattern hints
3. default to `article`

Examples:

- many cards => `listing`
- product-detail signals => `detail`
- URL suggests `wikihow`/`how-to`/`instructables` and multiple step elements => `howto`
- path includes `/shop/`, `/category/`, `/search`, etc. with enough cards => `listing`
- `/products/<slug>` without trailing `/` => `detail`
- otherwise => `article`

This is a pragmatic classifier designed for shop and content websites, not a general semantic page-type system.

---

## 6) Structured data extraction

### `_extract_json_ld(soup)`

This looks for `<script type="application/ld+json">` blocks and parses them as JSON.

For each block:

- if it is a dict, it formats it with `_format_json_ld()`
- if it is a list, it formats each item individually
- it ignores invalid JSON cleanly

This is often a rich source of facts for product pages, articles, FAQs, recipes, reviews, etc.

### `_format_json_ld(data)`

This is a type-aware formatter for common JSON-LD schemas:

- Product
- Recipe
- FAQPage
- HowTo
- Event
- Review
- Article / NewsArticle / BlogPosting
- Person / Organization
- generic fallback

It emits flattened facts like:

- `Type: Product`
- `Name: X`
- `Brand: Y`
- `Rating: 4.8`
- `Price: $299`
- `Author: ...`
- `Q: ...`
- `A: ...`

It also handles nested `offers`, `reviewRating`, and other repeated structures.

### `_extract_svg_text(soup)`

It searches for `<svg>` elements and captures:

- direct text within the SVG
- `<title>` text
- `<desc>` text

This helps with charts, icons, and diagrams that contain meaningful text.

### `_extract_data_attributes(soup)`

It walks all elements and collects any attribute that starts with `data-`.

This is a useful fallback for pages that embed semantic metadata in custom data attributes instead of standard schema.

---

## 7) Structured field extraction

### `_is_data_table(table)`

This is a heuristic designed to tell the difference between a real data table and a layout table.

It checks:

- at least 2 rows
- a consistent number of columns across rows
- column count within a sane range (`2..20`)
- median cell length under a threshold
- not too many block-level child tags inside cells

This is the “real table vs. navigation layout box” filter.

### `_extract_tables_structured(soup)`

For each candidate `<table>`:

- takes headers from `<thead>` if present
- otherwise uses the first row as a header candidate
- iterates rows and extracts values
- drops rows with empty cells
- stores a dict with:
  - `headers`
  - `rows`
  - `row_count`
  - `col_count`
  - `heading`
  - `heading_path`

This gives downstream code a clean table structure rather than raw HTML.

### `_extract_lists_structured(soup)`

For every `<ul>` and `<ol>`:

- extracts each `<li>` recursively
- keeps only non-empty item text
- attaches the nearest heading
- stores `type`, `items`, `item_count`, and heading metadata

This picks up bullet and numbered lists as standalone data objects.

### `_extract_cards_structured(soup)`

This extracts product-card-like blocks and stores small dicts with:

- `name`
- `description`
- `price`
- `text`

This is a lighter structured version of the more detailed card extraction used for listing pages.

---

## 8) Media chunk creation

### `_create_media_chunks(media_assets, page_title, url)`

This is a helper for image-driven content. It does not run in the main `process_html()` path, which is an important observation. It is present but effectively dormant from the current entry point.

It processes image assets only if:

- `media_type == 'image'`
- `was_analyzed` is true
- description length is at least `MIN_IMAGE_DESC_CHARS`
- source URL is not a decorative asset like a logo or icon
- description is not a flat-color placeholder or empty image marker
- the image has either visible text or detected entities

Then it builds a pseudo-chunk like:

- heading: `Image`
- heading path: `[page_title, 'Image']`
- content: description + visible text + entities + alt text + caption + section heading

This is a quality gate to prevent junk images from becoming retrieval content.

Important note: this method is defined but not actually called from `process_html()`. So media chunk generation is currently more of a planned capability than an active final output path.

---

## 9) Card extraction for listing pages

### `_validate_card_container(elements)`

This checks whether a repeated container is likely a real card group rather than a generic layout wrapper.

A valid card container should have many elements that:

- have meaningful text
- include a link, heading, or price-like value

This is a heuristic used to auto-detect product grids and similar listing structures.

### `_extract_cards(soup, url, page_title)`

This is the listing-page extraction pass.

It does the following:

1. Tries known selectors such as:
   - `[data-product]`
   - `.product-card`
   - `.item-card`
   - `li.product`
   - `div.product`
2. If no direct selectors match, it falls back to scanning class names and trying the most common non-layout class that looks like a card container.
3. For each candidate card:
   - picks the heading name from `h1`..`h6`, title/name-like class, or first link
   - extracts a price
   - extracts description
   - extracts link href
   - assembles a readable card block
   - skips junk content
4. Appends each card as a section with a synthetic heading path like `[Page Title > Card Name]`

The result is a list of sections representing product entries or item cards.

This is the clearest “listing page” handling in the file.

---

## 10) How-to page extraction

### `_extract_howto_content(soup, url, page_title)`

This branch targets pages with step-by-step instructions.

It tries to find a likely “main content” wrapper:

- `#main-content`
- `.main-content`
- `#article-body`
- `.article-body`
- `<article>`
- `<main>`
- then falls back to the whole document

Then it looks for step-like sections:

- `div.steps`
- headings matching `part|step` patterns

For each such section, it walks forward sibling elements until the next heading and accumulates:

- paragraph text
- list items
- nested div blocks
- tables

Then it converts that into a structured section with heading path `[Page Title > Section Title]`.

If no dedicated how-to sections are found, it falls back to `_extract_full_dom()`.

This is a strong page-type-specific extraction mode.

---

## 11) Full DOM extraction and deduplication

### `CONTENT_ROOT_SELECTORS`

This is the ordered list of likely content roots:

- `main`
- `article`
- `[role="main"]`
- `#content`, `#main`, `#main-content`, etc.
- `.content`, `.main-content`, `.post-content`, etc.
- `.container`

### `_find_content_root(soup)`

This chooses the DOM node that looks like the actual content region.

It tries the selectors in order and selects the first one whose inner text has at least `CONTENT_ROOT_MIN_WORDS` words. If none work, it falls back to `<body>`.

This is a practical content-root selector designed to avoid parsing navbars, headers, and noisy wrappers.

### `_extract_full_dom(soup, url, page_title)`

This is the backbone extraction loop.

It does the following:

1. Creates `sections = []`
2. Tracks `seen_hashes` to avoid duplicate section bodies
3. Tracks `processed_elements` to skip re-processing the same DOM node
4. Sets up a `current_heading` and `heading_path`
5. Uses a `flush()` helper to emit grouped content when a heading or section ends

It walks the content root and processes only elements among:

- `h1..h6`
- `p`
- `div`
- `section`

It ignores elements within:

- `nav`
- `header`
- `footer`
- `aside`
- roles like `navigation`, `banner`, or `contentinfo`

It also ignores content inside `<table>`, `<ul>`, and `<ol>` because those are extracted separately.

Important behavior:

- When a heading is found, it flushes the current content and updates `current_heading`
- For divs, it only contributes text if the div is a leaf-like container that doesn’t already contain a block-level child that will be processed separately
- Each section is emitted only if it is not junk and above the minimum size threshold
- It hashes the normalized text to dedupe exact repeats

This creates a clean section list with heading-based grouping.

---

## 12) Metadata and title extraction

### `_resolve_title(soup, page_title, source_url)`

The logic is straightforward:

- explicit page title if given
- `<title>`
- first `<h1>`
- `meta property="og:title"`
- fallback URL or `Untitled Page`

This gives the page a stable display title across different site structures.

### `_extract_metadata(soup, url)`

This pulls basic page metadata:

- URL
- domain
- path
- title
- meta description
- OG title
- OG description

It stores them in a dictionary the final result includes.

---

## 13) SPA shell detection

### `_detect_spa_shell(soup)`

This function detects a page that is mostly a shell for JavaScript rendering:

- empty root app divs
- React hydration attributes
- low visible word count with many scripts

This is a useful guard against treating an empty shell as real article content.

If the shell is detected, the class returns an empty result with `page_type='spa_shell'` before any real extraction begins.

---

## 14) The empty-result contract

### `_empty_result(page_title, source_url, page_type='empty')`

This function returns a consistent empty dictionary for a failed or empty extraction.

It includes fields such as:

- `page_title`
- `source_url`
- `page_type`
- `main_content`
- `document_structure`
- `text_stats`

This ensures downstream code can treat blank pages consistently instead of crashing on missing keys.

---

## 15) What the final result looks like

The final output of `process_html()` is a dictionary shaped roughly like this:

- `page_title`
- `source_url`
- `page_type`
- `main_content`
  - `all_text`
  - `sections`
  - `headings`
  - `paragraphs`
  - `lists`
  - `tables`
  - `cards`
  - `has_content`
- `metadata`
- `document_structure`
- `text_stats`

The most important field is usually `main_content.all_text`, because that is the plain extracted content later consumed by chunkers and retrieval.

---

## 16) Real execution order in one sentence

The whole file follows this order:

1. parse HTML
2. detect SPA shell
3. collect structured metadata
4. strip scripts/styles
5. resolve page title
6. classify page type
7. extract main DOM sections
8. add listing/how-to-specific sections if needed
9. collect tables/lists/cards
10. add the meta description if worth keeping
11. join all content into `all_text`
12. return final normalized dictionary

---

## 17) Bugs, weak spots, and design risks

Here are the main issues I noticed while reading the file.

### Bug 1: hardcoded Linux debug path

`DEBUG_LOG_PATH = "/app/debug_content_processing.log"`

This is a container-path assumption. On a local machine or a non-Docker environment, writing to `/app/...` will fail silently in the `try/except` blocks and the debug logs will not be produced.

This is not fatal to extraction, but it breaks diagnostics in local development.

### Bug 2: `process_html()` never calls `_create_media_chunks()`

The method exists, but the main pipeline never invokes it. This means the media-analysis chunk generator is dead code unless some other caller manually invokes it.

So image-level informational chunks are currently not actually emitted in the normal extraction path.

### Bug 3: final output does not include all extracted structured data in a consistent way

The class extracts tables, lists, cards, and JSON-LD, but `all_text` is built only from `sections` plus `structured_text`.

That means the plain-text output may not contain all structured table/list/card content in a fully retrievable form, even though those objects are stored separately in `main_content` and `document_structure`.

This is not necessarily a crash bug, but it is a data-loss / retrieval-quality issue.

### Bug 4: duplicate information can be repeated

The file intentionally merges and deduplicates sections, but it also prepends JSON-LD and metadata to `all_text` before the article text. That can create duplicate facts when the same value appears both in structured data and visible article text.

This may increase token count without adding much value.

### Bug 5: not all listing or detail pages are correctly classified

The page-type heuristics are pragmatic but not robust.

For example:

- some legitimate article pages may be misclassified as listings if they happen to repeat card-like classes
- some product detail pages could still fall through to `article` if the DOM signatures are weak
- URL and DOM heuristics can disagree and the logic may depend heavily on site structure conventions

This is a heuristic risk rather than a deterministic bug.

### Bug 6: relative links in card extraction are reconstructed weakly

In `_extract_cards()`, relative `href` values are converted to an absolute URL using:

`scheme://netloc + href`

This is okay for simple relative links but it can be wrong for:

- URLs with existing path prefixes
- `href="/products/cat/"` where the domain is already part of a subpath
- weird base-tag cases
- protocol-relative URLs like `//cdn.example.com/x`

It is serviceable but fragile.

### Bug 7: `_detect_spa_shell()` may flag normal thin pages as shells

A legitimate thin content page with few words and many scripts could be mistaken for a shell. This is especially possible for landing pages, short product pages, or pages that rely heavily on JS but still have real content.

This is a classification tradeoff: better false-negative but some false positives are possible.

### Bug 8: the logic is optimized for a specific class of websites

This file is strongest for:

- e-commerce pages
- product listings
- article pages with visible headings
- how-to pages

It is weaker for:

- single-page apps with minimal server-rendered content
- heavily nested CMS layouts
- pages with custom CSS classes and unusual DOM semantics
- pages that hide content in scripts or templates

That is not a bug per se, but it means the architecture is opinionated and site-structure dependent.

---

## 18) Overall assessment

This file is a strong DOM-based extraction layer with a very clear purpose: normalize a website into a page bundle that is ready for retrieval.

Its strengths are:

- explicit page-type branching
- section deduplication
- table/list/card extraction
- metadata preservation
- SPA shell detection
- strong fallback behavior for HTML-heavy sites

Its weaknesses are:

- hardcoded log path
- dead media-chunk generation path
- heuristic classification tradeoffs
- structural assumptions about site markup
- information duplication in the final text output

In other words, it is a good extraction engine for real-world websites, but it is still a heuristic system, not a universal parse engine.

This class is best understood as a page-normalization and extraction front-end for the rest of the scraper pipeline rather than the final stage of semantic understanding.
