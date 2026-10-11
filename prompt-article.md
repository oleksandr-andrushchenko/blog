# SysDesPro Technical Article Prompt

## Task

Create a production-quality, ready-to-publish HTML article for **SysDesPro** about the requested topic.

Write for experienced software engineers, backend engineers, cloud and DevOps engineers, architects, and system-design
readers. Assume the reader already understands software development fundamentals.

The article must explain real engineering decisions rather than rewrite vendor documentation. Prioritize the most
important concepts, production trade-offs, implementation details, failure behavior, and operational concerns. Explain *
*why**, not only **how**.

Prefer depth over breadth. Keep the article comprehensive but concise enough to fit in one response whenever possible.
Do not pad it or attempt to cover every possible aspect of the topic.

## Execution Rules

- Start with the article immediately. Do not discuss the prompt, limitations, or response length.
- Generate one complete article in one response whenever possible.
- If the response limit is reached, stop only at the end of a complete section. When the user says `continue`, resume
  from that exact point without repeating content or adding commentary.
- Do not apologize, ask for confirmation, or explain why generation stopped.
- Output only the requested article HTML. Do not add notes before or after it.

## Output Format

Generate only the inner HTML of the `<article>` element.

Do not generate:

- `<html>`
- `<head>`
- `<body>`
- `<article>`
- `<h1>`

The result must be valid, immediately publishable HTML.

## Hero Image and Introduction

The first element must be:

```html
<img src="/seo-friendly-image-name.png"
     alt="Article Title"
     class="img-fluid rounded">
```

Use a meaningful, SEO-friendly filename based on the article title.

After the image, include one or two short introductory paragraphs that:

- define the article's scope;
- explain the problem or concept;
- explain why it matters in production;
- avoid generic filler.

Do not create an `Introduction` heading.

## Article Structure and Heading Hierarchy

Determine the clearest structure for the specific topic before writing. The prompt suggests useful subjects and
organizational patterns, but it does **not** prescribe a fixed outline or a required number of sections.

Use as many or as few sections as the article genuinely needs. A short, focused article may need only a few H2 chapters;
a broader comparison may need more. Article quality, conceptual clarity, and natural progression are more important than
any heading count.

- Use `<h2>` only for a major conceptual chapter.
- Use `<h3>` only when it meaningfully organizes related material inside an H2.
- Explain smaller points in paragraphs, lists, tables, or examples instead of creating headings for all of them.
- Group related topics to avoid a long, flat list of headings.
- Every H2 must contain explanatory text before its first H3.
- Never place headings directly beside each other without explanatory content.
- Use only H2 and H3 headings.
- Give every heading a unique, descriptive, kebab-case `id`.

For example, prefer:

```text
H2 How AI Works
    H3 Training
    H3 Inference
    H3 Model Evaluation
```

instead of:

```text
H2 Training
H2 Inference
H2 Model Evaluation
```

A possible progression is fundamentals → internal behavior or architecture → production considerations → conclusion.
This is only a suggestion. Use another progression whenever it explains the topic better.

Content requirements are **not automatically headings or mandatory standalone sections**. Common mistakes, production
checks, advantages, disadvantages, security, monitoring, failure scenarios, scalability, and similar subjects should be
covered only when relevant. Present them as paragraphs, lists, tables, examples, H3s, or H2s according to their
importance in the specific article.

## Table of Contents

After the introduction, include:

```html
<h2 id="table-of-contents">Table of Contents</h2>
```

The Table of Contents must:

- include every H2 and H3, including `More Articles to Read`;
- link to each heading by its `id`;
- use a nested `<ul>` for H3 entries;
- exactly match the final article structure.
- a tags in ToC can have href attribute only

## Possible Structural Patterns

The following patterns are suggestions, not templates. Select, combine, reorder, or ignore them based on the topic:

- **Foundational topic:** concept → how it works → examples → practical implications → production use.
- **Architecture topic:** problem → design → request or data flow → trade-offs → failures → operations.
- **Comparison topic:** major options → trade-offs → comparison table → decision guidance.
- **Implementation topic:** problem → design → implementation → failure handling → monitoring → scaling.

When comparing major technologies, strategies, or approaches, give each option reasonably balanced coverage. When
useful, explain its operation, advantages, disadvantages, suitable use cases, limitations, and production
considerations. Add a comparison table only when it makes the decision clearer.

Do not repeat the same sequence of subsections for every concept. The LLM must choose the final structure and
presentation that produce the strongest article.

## Possible Technical and Production Coverage

Cover only the dimensions that materially affect the topic. The following are possibilities, not a checklist of
mandatory sections or paragraphs:

- architecture and internal behavior;
- latency, throughput, and bottlenecks;
- horizontal and vertical scaling;
- availability, reliability, consistency, and fault tolerance;
- resource utilization and capacity planning;
- failure modes, recovery, retries, and failover;
- deployments, migrations, and disaster recovery;
- monitoring and observability;
- security and validation;
- operational complexity and cost.

Do not merely state that something is fast, reliable, or scalable. Explain what limits it, what becomes saturated, what
can fail, how the impact is contained, and what trade-offs the solution introduces.

Never describe only benefits. Explain when an approach should and should not be used.

## Writing Style

Write like an experienced production engineer explaining the subject to another engineer.

- Use clear, direct, natural language.
- Keep paragraphs short—usually one to three sentences—with one main idea per paragraph.
- Make the article easy to scan without making it shallow.
- Use `<b>` selectively for important concepts, rules of thumb, trade-offs, warnings, and actions.
- Prefer concrete explanations and realistic examples over abstract theory.
- Avoid repetition.

Do not use:

- marketing language;
- academic or vendor-documentation tone;
- unnecessary history;
- beginner-tutorial filler;
- artificial storytelling;
- long introductions;
- `we`, `our`, `you`, `your`, or `let's`.

## Practical Examples and Code

Include practical examples when they materially improve understanding. Place each example inside the section containing
the concept it demonstrates.

Prefer realistic production domains such as APIs, microservices, logistics, payments, authentication, messaging, cloud
infrastructure, databases, caching, search, and AI systems.

Code and configuration examples may use Python, SQL, TypeScript, JavaScript, YAML, JSON, Bash, Docker, Kubernetes, or
CloudFormation. Choose the language that best fits the concept.

Code must:

- demonstrate an engineering decision rather than syntax alone;
- be logically correct and internally consistent;
- include imports, types, constraints, or comments when they improve completeness;
- use realistic names and behavior rather than `hello world` examples;
- be escaped correctly for HTML.

Use language-specific code blocks:

```html

<pre class="language-python"><code>...</code></pre>
```

Explain the production reasoning around substantial code examples.

Do not create a separate `Ready-to-Use Example` section when examples already fit naturally into relevant sections. Use
an integrated implementation section only when it combines several earlier ideas into one useful production design.

## Tables, Lists, and Diagrams

Use tables for genuinely comparative information such as trade-offs, performance, consistency, cost, failure behavior,
or decision criteria. Keep tables focused and reasonably sized.

Always use:

```html

<table class="table">
```

Use lists for naturally enumerable information. Do not create a table when a short paragraph or list is clearer.

### Diagrams

Do **not** generate architecture diagrams, ASCII art, vertical flows, trees, boxes, or large text illustrations.

If a flow is essential and prose would be substantially less clear, use only one very small **inline horizontal flow**
inside a text code block, for example:

```html

<pre class="language-text"><code>Client → API → Queue → Worker → Database</code></pre>
```

Keep it on one line, use only component names and arrows, and omit it entirely unless it provides clear value. Prefer
prose, a list, a table, or code in all other cases.

## Optional Production Guidance

When failure behavior is important to the topic, explain some or all of the following in the format that reads most
naturally:

- what fails;
- what the caller or upstream system experiences;
- how the design limits the impact;
- how recovery occurs;
- which metric would reveal the problem.

Use concrete observability signals rather than generic advice. Examples include p50/p95/p99 latency, throughput, error
rate, saturation, queue depth, retry rate, cache hit ratio, connection-pool utilization, replication lag, storage
growth, and cost per request.

### Common Mistakes

Common mistakes are optional. Include them only when they add real production value. They may be ordinary paragraphs, a
concise list, a focused table, an H3, or—only when central to the topic—an H2.

Each mistake must describe:

- a realistic engineering error;
- its architectural, operational, reliability, performance, scalability, security, or cost impact;
- one concrete better approach.

Avoid vague advice such as “test more,” “monitor everything,” or “optimize performance.” Do not repeat material already
explained elsewhere.

### Production Checklist

A production checklist is optional. Include one only when it adds concrete actions beyond the main discussion. Its
guidance may instead be integrated into relevant paragraphs. If a checklist is useful, place it where it reads
naturally; make it a standalone section only when it is substantial.

Each item must:

- begin with a bold action or concept;
- describe one immediately applicable action;
- remain concise and topic-specific;
- avoid repeating Common Mistakes verbatim.

Example:

```html

<li><b>Monitor replication lag.</b> Alert before lag exceeds the application's acceptable stale-read window.</li>
```

## Cross-References

When another SysDesPro article covers a secondary topic in depth, explain only enough to keep the current article
self-contained, then optionally add a short contextual link at the end of the relevant paragraph. Use descriptive
article-title anchor text.

Do not add cross-references mechanically or invent articles merely to create links.

This urls can help to find similar/existing articles to make real references:

List all the article as url => title (you should cache this call):
https://api.sysdespro.com/articles

also, you can use non-existent articles from the current article series if they were created in chat already

You can use https://api.sysdespro.com/categories to advice a category for the article

## HTML Rules

Use only Bootstrap-compatible HTML and these supported elements:

```text
img[src|alt|class]
h2[id], h3[id]
a[href|target|title]
b, i, em, u, span[class]
ul, ol, li
table[class|border|cellpadding|cellspacing], thead, tbody, tfoot, tr,
th[colspan|rowspan], td[colspan|rowspan]
div[class], br, p, pre[class], code[class], blockquote
```

Allowed Bootstrap classes:

- `table`
- `img-fluid`
- `rounded`

Use `<b>` instead of `<strong>`. Avoid custom CSS classes and unnecessary wrapper elements.

## Conclusion and Related Articles

Finish with a short `<h2 id="conclusion">Conclusion</h2>` section that summarizes the most important engineering
lessons, production trade-offs, and decision principles. Do not introduce major new concepts.

End the article with:

```html
<h3 id="more-articles">More Articles to Read</h3>
<ul>
    <li><a href="...">Related Article</a></li>
</ul>
```

Include only closely related SysDesPro articles;
This urls can help to find similar/existing articles to make real references:

List all the article as url => title (you should cache this call):
https://api.sysdespro.com/articles

also, you can use non-existent articles from the current article series if they were created in chat already
Each link should be unique across the article, so if something was already mentioned - we dont include it.

## FAQ Section

Optionally include an `<h2 id="faq">Frequently Asked Questions</h2>` section when the article topic naturally has common
practical questions that are useful to answer separately. Do not add an FAQ just to satisfy a template. When included,
add approximately 3–6 concise questions using `<h3>` headings, followed by short, direct answers of 1–3 paragraphs.
Questions should address real reader concerns, clarify common misunderstandings, or cover practical details not already
fully answered in the main article. Avoid repeating the article content simply in question-and-answer form. Include the
FAQ and each FAQ question in the Table of Contents.

## Final Quality Check

Before returning the article, silently verify that:

- the structure was chosen for the topic rather than copied from a fixed template;
- every heading is necessary and smaller ideas remain paragraphs, lists, tables, or examples;
- the Table of Contents exactly matches all headings;
- every heading has a unique kebab-case `id`;
- no H2 is immediately followed by an H3;
- all internal links use the required user slug;
- examples are realistic and placed near the concepts they demonstrate;
- production concerns are integrated naturally rather than added as repetitive mandatory sections;
- no prohibited diagrams were included;
- the HTML uses only supported tags and classes;
- the response contains only the article HTML.