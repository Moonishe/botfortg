# Devil's Advocate: what could go wrong

## 1. The reference corpus is not maintained
The bundled `html-effectiveness` README says explicitly: "Sample code. Not maintained and not accepting contributions." The skill tells the model to "review the files throughout references/html-effectiveness/" and match them. If the model is trained on newer web standards or the corpus becomes stale, the style may drift or look dated. The license mismatch (MIT top-level vs Apache-2.0 for the corpus) is harmless but worth noting when redistributing.

## 2. No tests, no validation, no runtime
There is no schema, no HTML validator, no snapshot tests, no rendering tests. The skill produces arbitrary HTML; the host has no way to know whether the output is valid, accessible, or follows the style guide. The only enforcement is the prompt itself.

## 3. Accessibility is not guaranteed
The prompts mention SVG `aria-label`, `role="img"`, and `prefers-reduced-motion`, but the instructions do not require ARIA landmarks, color-contrast checks, keyboard navigation, or semantic HTML. Generated diagrams may be unusable for screen-reader users unless the model explicitly adds it.

## 4. Security model of generated HTML
The skill outputs self-contained HTML with inline JS and CSS. If the agent renders user-provided data, there is an XSS risk unless the model escapes content. The prompt does not tell the model to sanitize input or avoid inline event handlers. The `tot` sharing tool and the `plannotator` review surface mitigate this by sandboxing iframes, but the skill itself is neutral.

## 5. Theme coupling is brittle
The dark-mode requirement is hand-rolled CSS variables on `:root` / `html.dark`. This is a strong convention but not enforced. A model could forget the toggle, forget `localStorage`, or use Tailwind/other frameworks. The prompt says "never hard-coded hex inside the SVG" for diagrams, but the general `html` skill does not forbid external frameworks.

## 6. Plugin/installation fragmentation
The repo ships four different plugin manifests (skills.sh, Claude, Codex, .agents/plugins). Each has a slightly different shape, no shared schema tests, and no CI visible in the repo. A drift in one manifest (e.g., skill path) could break installation in one IDE.

## 7. No versioning or changelog
26 commits, no releases, no CHANGELOG. The README shows 1.1k stars, 77 forks, but the project is young. Breaking changes in skill format or manifest schema would be hard to discover.

## 8. Heavy reliance on model quality
The skill is a few hundred words of prompt plus 20 example files. The actual output quality depends entirely on the model's ability to imitate the examples. Smaller models may produce worse SVGs, miss the theme script, or generate verbose prose instead of the requested visual style.

## 9. Duplicated corpus across skills
Each skill bundles the entire `html-effectiveness` corpus (20 files). This triples the size of the skill package and could lead to divergence if one skill is updated and the others are not. The README mentions the original design was `references/html-effectiveness/` under the repo root, but the current structure has it under each skill.

## 10. Hard to compose with other outputs
The skill is optimized for single-file HTML deliverables. If a Telegram bot needs to send an HTML preview, an image, and a PDF, the skill does not address multi-modal output. It also does not include email templates, mobile-first layouts, or print stylesheets.

## Tools used
- `webfetch` on the README and the bundled corpus README/SECURITY/LICENSE.
- `webfetch` on the `architecture-example.html` and `16-implementation-plan.html` to inspect for a11y, theme, and security patterns.
