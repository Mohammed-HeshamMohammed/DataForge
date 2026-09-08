# Architecture

DataForge is one Python package with six layers. Dependencies point strictly
downward — `core` knows nothing about the web, and the web layer knows nothing
about Selenium — so any layer can be used on its own.

```
        ┌──────────┐  ┌─────┐  ┌─────────┐
        │ web (UI) │  │ CLI │  │ desktop │      front doors
        └────┬─────┘  └──┬──┘  └────┬────┘
             └───────────┼──────────┘
                    ┌────▼─────┐
                    │pipelines │                composition + scheduling
                    └────┬─────┘
        ┌────────────┬───┴────┬──────────────┐
   ┌────▼─────┐ ┌────▼───┐ ┌──▼──┐ ┌─────────▼──┐
   │ scraping │ │  core  │ │ ml  │ │ automation │  capability layers
   └──────────┘ └────────┘ └─────┘ └────────────┘
```

## The layers

**`core`** is the only layer with no internal dependencies. It holds
`ColumnRoles` (the vocabulary every other layer speaks), file I/O,
normalisation, the comparison rules, and the `Deduplicator`.

**`ml`** trains a classifier over the same role-based comparisons `core` uses.
Because `MatchModel.score_pairs` has the same signature as the rule scorer, a
trained model drops into `Deduplicator(scorer=model)` with no other change —
this is the one abstraction the whole ML layer hangs on.

**`scraping`** defines a `Spider` whose only required method is `crawl()`.
`HttpSpider` supplies rate limiting, retries, a declared user agent and a
robots.txt check, so a new source is written by overriding `parse()` alone.
Spiders self-register by name via a decorator.

**`automation`** turns a YAML recipe into browser actions. Selenium is imported
lazily and only inside the runner, so the package installs and every other layer
runs on a machine with no browser.

**`pipelines`** composes the above. A step is any callable taking and returning
a `PipelineContext`; a `Pipeline` runs steps in order, records timings, and
stops at the first failure with the error attached to the run. `Scheduler`
wraps APScheduler for cron-driven runs.

**Front doors** (`web`, `cli`, `desktop`) contain no matching logic. They
collect inputs, call a pipeline or the engine, and render the result.

## Extension points

| To add… | Do this |
| --- | --- |
| A data source | Subclass `HttpSpider`, override `parse`, decorate with `@register_spider` |
| A target system | Write a YAML file in `recipes/` |
| A scoring strategy | Implement the `PairScorer` protocol (two methods, one signature) |
| A workflow | Compose steps into a `Pipeline`, call `register_pipeline` |
| A file format | Add a branch to `core/io.py` and its suffix to the format sets |

## Optional dependencies

Heavy dependencies are extras, and every import of one is deferred to the
function that needs it. Installing only `dataforge[web]` gives a working web
service; asking it to score with a model then fails with an actionable message
rather than an ImportError at startup.
