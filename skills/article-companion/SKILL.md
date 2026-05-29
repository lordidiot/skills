---
name: article-companion
description: Read an article from a URL into working context before answering reading questions. Use when the user provides an article, essay, documentation page, blog post, news story, paper-like web page, or browser annotation tied to an article URL and wants quick explanations, clarifications, summaries, references, or follow-up Q&A while reading.
---

# Article Companion

## Goal

Act as a reading companion for one article at a time. Optimize first for getting the article visible to the user, then for building complete article context, then for answering quickly and accurately.

## Initial Setup

1. First action: open the URL in the visible Codex in-app browser so the user can begin reading immediately.
2. Do not spend the first action extracting content, summarizing, inspecting metadata, using Computer Use, or controlling Chrome if the article is not visible in the Codex in-app browser yet.
3. After the article is visible, perform non-disruptive extraction in this order: `web.run`, then optional `curl`, then separate browser/context tab only if needed.
4. Read the entire article body, including sections below the fold. Do this without disturbing the visible reading surface whenever possible.
5. Ignore unrelated page chrome unless it affects comprehension: nav bars, cookie banners, ads, sidebars, recommendation widgets, comments, newsletter prompts, and footer links.
6. Build a compact internal map of the article: thesis, major sections, important claims, examples/evidence, definitions, caveats, and any open questions.
7. Briefly tell the user that the article is loaded only after the extraction sufficiency checklist passes. Do not dump a long summary unless asked.

## Open The Article

Use the Browser plugin through the Node REPL. Do not use Computer Use for this workflow; it cannot control Codex itself and may trigger unrelated Chrome approvals.

Run one first browser cell with the actual article URL substituted for `ARTICLE_URL`. Use the installed Browser plugin's `browser-client.mjs`; the path below is the current bundled path in this environment:

```js
if (!globalThis.agent) {
  const { setupBrowserRuntime } = await import("/Users/idiot/.codex/plugins/cache/openai-bundled/browser/26.519.81530/scripts/browser-client.mjs");
  await setupBrowserRuntime({ globals: globalThis });
}
if (!globalThis.browser) {
  globalThis.browser = await agent.browsers.get("iab");
}
await browser.nameSession("📖 Article companion");
await (await browser.capabilities.get("visibility")).set(true);
if (typeof tab === "undefined") {
  globalThis.tab = await browser.tabs.new();
}
await tab.goto("ARTICLE_URL");
```

After this cell succeeds, the page is visible to the user. Only then continue with extraction. If the Node REPL `js` tool is not available, use tool discovery for `node_repl js`; do not fall back to Computer Use unless Browser tool discovery fails, the in-app browser cannot be acquired, or the Browser runtime needs user intervention.

## Reading Standard

Treat “read the article” as requiring the full main content, not just the visible viewport or annotation snippet. If the page is long, paginated, lazy-loaded, or infinite-scrolling, keep navigating or scrolling until the end of the main article is clear.

If the page has a table of contents or section navigation, use it to verify coverage. If a paywall, login wall, script failure, unsupported page, or network issue prevents full reading, say exactly what was accessible and what was blocked before answering.

## Extraction Strategy

Do not scroll, navigate, or otherwise move the visible in-app browser page the user is reading unless there is no viable alternative.

Prefer this extraction order:

1. Use `web.run` page opening or another first-class web page reading tool when available. This is the preferred non-disruptive first pass for public articles.
2. If a first-class web page reader is unavailable, use `curl -L` or an equivalent direct fetch only when network approval is expected to be automatic or already allowed.
3. If direct extraction is missing important content, incomplete, blocked, or clearly pre-rendered without the article body, use a separate browser/context-gathering path if available.
4. Use the visible in-app browser for extraction only as a fallback, and warn the user briefly if doing so may move their reading position.

For JavaScript-heavy sites, paywalled pages, authenticated pages, or pages whose content differs from the fetched HTML, prefer a browser-based extraction path that does not interfere with the visible reading tab.

### Web Extraction First

After opening the visible browser, run a non-browser extraction pass before using any browser tab for reading.

Preferred path: use `web.run` to open the URL. Treat the returned page lines as the first extraction artifact. Identify the main article span by title/date/body and stop before comments, related posts, sidebars, archive lists, footer, or other chrome. Build `articleContext` from that page content:

- `source`: `web-run`
- `url` and canonical URL if visible
- title, author/source, and date if visible
- main article text
- text length, headings/section markers, and a compact article map

Do not repeatedly reopen the same URL with `web.run` during initial extraction. One attempt is normally enough; retry only if the first call clearly failed for a transient reason and returned no usable page content. For `web.run`, the sufficiency decision is qualitative: if the result includes the article title, the opening body, multiple later sections or paragraphs, and a plausible ending or transition into comments/footer, treat it as sufficient and stop. Preserve enough article body text in `articleContext.text` to answer follow-ups when possible; if the tool output cannot provide a clean raw text blob, store a compact structured map instead and note that exact quotation may be limited.

Fall back after `web.run` only when the page result is clearly incomplete or inaccessible: missing the main body, truncated before later sections, paywalled/login-blocked, mostly navigation/chrome, or contradicted by annotation/page metadata. Do not try another `web.run` open of the same URL before falling back.

Do not use Node global `fetch()` as the direct first pass; in Codex Desktop it may fail under local DNS/network restrictions without triggering approval.

### Optional Curl Extraction

If `web.run` is unavailable or incomplete and local network approval is expected to be fast, use shell `curl -L` as the next direct extraction attempt. On network/DNS failure, request escalation rather than retrying Node `fetch`.

For curl/raw HTML extraction, select likely article containers before falling back to body text. Useful candidates include:

```text
article
main
[role="main"]
.entry-content
.entry
.post-content
.post
.hentry
#content
#single-content
```

Strip scripts, styles, navigation, comments, related posts, sidebars, archives, tag clouds, and footer content. Produce the same `articleContext` shape as the web extraction path.

Example local extraction shell shape:

```bash
curl -L --max-time 20 -sS "ARTICLE_URL"
```

Treat the web/curl direct pass as sufficient only if it yields the apparent article title plus substantive article body text, not just nav/sidebar/link text. As a rough default, require at least 2,000 characters of coherent article prose unless the page is clearly shorter.

### Browser Fallback Extraction

Use a browser-based extraction path only when direct extraction is insufficient: too short, mostly navigation, blocked, missing the annotated page, missing expected selected/nearby text, or contradicted by visible page metadata.

Use a separate context tab or page handle when available. Do not use the visible `tab` that the user is reading. Example:

```js
if (typeof contextTab === "undefined") {
  globalThis.contextTab = await browser.tabs.new();
}
await contextTab.goto("ARTICLE_URL");
const browserArticleContext = await contextTab.playwright.evaluate(() => {
  const article = [...document.querySelectorAll("article, main, [role='main'], body")]
    .sort((a, b) => (b.innerText || "").length - (a.innerText || "").length)[0];
  const text = (article?.innerText || document.body.innerText || "").trim();
  const title = document.querySelector("h1")?.innerText?.trim() || document.title;
  const canonical = document.querySelector("link[rel='canonical']")?.href || location.href;
  const headings = [...document.querySelectorAll("h1,h2,h3")]
    .map(h => h.innerText.trim())
    .filter(Boolean)
    .slice(0, 30);
  return { source: "browser-context-tab", url: location.href, canonical, title, text, textLength: text.length, headings };
}, undefined, { timeoutMs: 10000 });
globalThis.articleContext = browserArticleContext;
console.log(JSON.stringify({
  source: articleContext.source,
  canonical: articleContext.canonical,
  title: articleContext.title,
  textLength: articleContext.textLength,
  headings: articleContext.headings,
  preview: articleContext.text.slice(0, 500),
  tail: articleContext.text.slice(-500)
}, null, 2));
```

If creating `contextTab` visibly switches the user's reading tab, stop using that fallback unless the user has not started reading yet or explicitly accepts the interruption. Prefer a partial-context warning over silently hijacking the visible page.

### Sufficiency Checklist

Do not tell the user the article is loaded unless all of these are true:

- An internal article context or article map exists in the current session.
- The article title is present or the page title is otherwise known.
- The retained text or map contains substantive article body prose.
- The captured content makes it plausible that the whole article was covered, including later sections.
- For annotation-triggered page changes, the annotation URL/canonical/title matches the retained article context, or you have re-extracted for the new page.

For `web.run` extraction, prefer real article body text in `articleContext.text`. If only a structured map can be retained, treat the checklist as satisfied only when the web page result had enough main-article content to support the map and no evidence of truncation.

If extraction fails or is partial, say it is blocked or partial before answering. Do not claim to have read the full article.

## Answering Questions

Answer only after full article extraction has completed, unless extraction is blocked or only partial. If the user asks a question while extraction is still in progress, acknowledge briefly, finish extraction first, then answer.

For annotation-driven questions, use the annotation text and metadata as the immediate focus, but still connect it to the full article context before answering.

Prefer concise, direct answers. When helpful, mention the relevant section, claim, or nearby passage in natural language. Avoid pretending to quote exact text unless the exact text is visible in context.

If the answer is not in the article, say so and distinguish article-grounded facts from outside inference. Browse/search beyond the article only when the user asks for external verification or the answer clearly requires current outside context.

## Page Changes

The user may move to a different page, article, or section while reading. On each annotation or follow-up, compare available metadata such as URL, canonical URL, title, frame URL, selected text, nearby text, or document context against the loaded article.

Reload and reread the full article when:

- The URL or canonical URL changes to a different article.
- The title/source changes in a way that suggests a new page.
- Annotation metadata references text or sections not present in the loaded article map.
- Pagination, “next page,” AMP/mobile variants, or translated versions make the current context stale.

When reloading because of a page change, repeat the same order: keep the visible article open, run direct extraction for the new URL first, then use the separate browser fallback only if direct extraction is insufficient.

If only the scroll position or selected passage changes within the same article, do not reread the whole article; use the existing full-article context plus the annotation.

## Operating Notes

Use the in-app browser as the primary visible reading surface. Keep it on the article URL when possible so later annotations line up with the same page.

When the prompt includes a URL, treat it as the article target. If multiple URLs appear, choose the one most likely to be the article and mention ambiguity only if it affects the task.

Maintain enough article context to answer rapid follow-ups, but keep user-facing responses short unless the user asks for a summary, outline, critique, or deep explanation.
