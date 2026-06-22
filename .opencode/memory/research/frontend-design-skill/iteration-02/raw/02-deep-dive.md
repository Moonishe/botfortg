# Deep Dive: anthropics/skills — frontend-design SKILL.md

**Source URLs:**
- GitHub raw (current/full): `https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/SKILL.md`
- skills.sh page (alternate/truncated version): `https://www.skills.sh/anthropics/skills/frontend-design`

**Fetched:** 2026-06-22
**Researcher:** Researcher 2 (Deep Dive)

---

## IMPORTANT: Two Distinct Versions Found

Two materially different versions of the SKILL.md were retrieved:

1. **skills.sh page version** (appears older/alternate): Contains an explicit aesthetic-directions list
   ("brutalist, maximalist, retro-futuristic, luxury, organic, etc.") and a "Design Thinking" section
   with Purpose/Tone/Constraints/Differentiation. This version was **truncated** at "Show more" on the
   skills.sh page, so only the first portion was captured.

2. **GitHub raw version** (current/complete, full text retrieved): Restructured into a "design lead at a
   small studio" framing. Does NOT include an explicit directions list. Instead names three "default AI
   looks" to AVOID and grounds direction in "the subject's own world, its materials, instruments,
   artifacts, and vernacular." Contains the full two-pass process, token system, quality floor, and
   writing-in-design guidance.

Both are documented below. The GitHub raw version is treated as authoritative (it is the live file
served by `npx skills add`).

---

## 1. Full SKILL.md Content (GitHub raw — current/authoritative)

### Frontmatter
```
name: frontend-design
description: Guidance for distinctive, intentional visual design when building new UI or reshaping an
existing one. Helps with aesthetic direction, typography, and making choices that don't read as
templated defaults.
license: Complete terms in LICENSE.txt
```

### Persona framing
> "Approach this as the design lead at a small studio known for giving every client a visual identity
> that could not be mistaken for anyone else's. This client has already rejected proposals that felt
> templated, and is paying for a distinctive point of view: make deliberate, opinionated choices about
> palette, typography, and layout that are specific to this brief, and take one real aesthetic risk you
> can justify."

### "Ground it in the subject"
- If the brief doesn't pin down the product/subject, pin it yourself: name one concrete subject, its
  audience, and the page's single job.
- Use memory about the human's preferences, context, or prior designs as hints.
- "The subject's own world, its materials, instruments, artifacts, and vernacular, is where distinctive
  choices come from."
- Build with the brief's real content and subject matter throughout.

### Design principles (the de-facto pillars)

**Hero as thesis:** Open with the most characteristic thing in the subject's world — headline, image,
animation, live demo, interactive moment. "A big number with a small label, supporting stats, and a
gradient accent is the template answer, only use if that's truly the best option."

**Typography carries personality:** Pair display and body faces deliberately — "not the same families
you would reach for on any other project." Set a clear type scale with intentional weights, widths,
spacing. "Make the type treatment itself a memorable part of the design, not a neutral delivery vehicle."

**Structure is information:** Structural devices (numbering, eyebrows, dividers, labels) should encode
something true about the content, not decorate it. "Many generic designs use numbered markers (01 / 02
/ 03), but that's only appropriate if the content actually is a sequence." Question whether such choices
make sense before incorporating.

**Leverage motion deliberately:** Where/if animation serves the subject — page-load sequence,
scroll-triggered reveal, hover micro-interactions, ambient atmosphere. "An orchestrated moment usually
lands harder than scattered effects; choose what the direction calls for." Caveat: "sometimes less is
more, and extra animation contributes to the feeling that the design is AI-generated."

**Match complexity to the vision:** Maximalist directions need elaborate execution; minimal directions
need precision in spacing, type, detail. "Elegance is executing the chosen vision well."

**Consider written content carefully:** Copy can make a design feel as templated as the design itself.

### Process: "brainstorm, explore, plan, critique, build, critique again"

**Three default AI looks to AVOID (calibration):**
1. Warm cream background (near `#F4F1EA`) with high-contrast serif display and terracotta accent.
2. Near-black background with a single bright acid-green or vermilion accent.
3. Broadsheet-style layout with hairline rules, zero border-radius, dense newspaper-like columns.

> "All three are legitimate for some briefs, but they are defaults rather than choices, and they appear
> regardless of subject. Where the brief pins down a visual direction, follow it exactly — the brief's
> own words always win, including when it asks for one of these looks. Where it leaves an axis free,
> don't spend that freedom on one of these defaults."

**TWO-PASS PROCESS (verbatim structure):**

> "Work in two passes. First, brainstorm a short design plan based on the human's design brief: create a
> compact token system with color, type, layout, and signature."

Token system components:
- **Color:** "describe the palette as 4–6 named hex values."
- **Type:** "the typefaces for 2+ roles (a characterful display face that's used with restraint, a
  complementary body face, and a utility face for captions or data if needed)."
- **Layout:** "a layout concept, using one-sentence prose descriptions and ASCII wireframes to ideate
  and compare."
- **Signature:** "the single unique element this page will be remembered by that embodies the brief in
  an appropriate way."

> "Then review that plan against the brief before building: if any part of it reads like the generic
> default you would produce for any similar page (work through a similar prompt to see if you arrive
> somewhere similar) rather than a choice made for this specific brief — revise that part, say what you
> changed and why. Only after you've confirmed the relative uniqueness of your design plan should you
> start to write the code, following the revised plan exactly and deriving every color and type decision
> from it."

Additional code-writing note: "be careful of structuring your CSS selector specificities. It's easy to
generate CSS classes that cancel each other out (especially with a type-based selector like .section
and an element-based selector like .cta). This can happen often with paddings/margins between sections."

> "Try to do a lot of this planning and iteration in your thinking, and only show ideas to the user when
> you have higher confidence it'll delight them."

### Restraint and self-critique
- "Spend your boldness in one place." Let the signature element be the one memorable thing; keep
  everything around it quiet and disciplined; cut decoration that does not serve the brief.
- "Not taking a risk can be a risk itself!"
- **Quality floor (build to it without announcing it):**
  - Responsive down to mobile
  - Visible keyboard focus
  - Reduced motion respected
- "Critique your own work as you build, taking screenshots if your environment supports it — a picture
  is worth 1000 tokens."
- Chanel's advice: "before leaving the house, take a look in the mirror and remove one accessory."
- If you have space to jot down notes about what you've tried, it can help in future passes.

### More on writing in design
- Words appear to make the design easier to understand and use — "design material, not decoration."
- "Write from the end user's side of the screen. Name things by what people control and recognize, never
  by how the system is built." (A person manages notifications, not webhook config.)
- "Describe what something does in plain terms rather than selling it. Being specific is always better
  than being clever."
- Active voice as default. A control should say exactly what happens: "Save changes," not "Submit."
- Action name consistency: button "Publish" → toast "Published."
- "Treat failure and emptiness as moments for direction, not mood." Errors don't apologize, never vague.
- "An empty screen is an invitation to act."
- Register: conversational and tuned — plain verbs, sentence case, no filler, tone matched to brand
  and audience. "Let each element do exactly one job."

---

## 2. skills.sh Page Version (alternate/truncated)

### Summary (from skills.sh metadata)
- "Distinctive, production-grade frontend interfaces that reject generic AI aesthetics through
  intentional design choices."
- "Guides aesthetic direction selection (brutalist, maximalist, retro-futuristic, luxury, organic, etc.)
  before implementation to ensure cohesive, memorable designs."
- "Emphasizes typography, color theming with CSS variables, motion design, spatial composition, and
  textural details as core design pillars."
- "Generates working code (HTML/CSS/JS, React, Vue) that matches implementation complexity to the
  aesthetic vision, from refined minimalism to elaborate maximalism."
- "Explicitly avoids overused fonts, clichéd color schemes, and predictable layouts."

### Design Thinking section (truncated at "Show more")
- **Purpose:** What problem does this interface solve? Who uses it?
- **Tone:** "Pick an extreme: brutally minimal, maximalist chaos, retro-futuristic, organic/natural,
  luxury/refined, playful/toy-like, editorial/magazine, brutalist/raw, art deco/geometric,
  soft/pastel, industrial/utilitarian, etc. There are so many flavors to choose from. Use these for
  inspiration but design one that is true to the aesthetic direction."
- **Constraints:** Technical requirements (framework, performance, accessibility).
- **Differentiation:** What makes this UNFORGETTABLE? What's the one thing someone will remember?
- **CRITICAL:** "Choose a clear conceptual direction and execute it with precision. Bold maximalism and
  refined minimalism both work — the key is intentionality, not intensity."

### Metadata
- Installs: 574.5K
- Repository: anthropics/skills (GitHub Stars: 153.5K)
- First Seen: Jan 19, 2026
- Security Audits: Agent Trust Hub Pass, Socket Pass, Snyk Pass
- Install: `npx skills add https://github.com/anthropics/skills --skill frontend-design`

---

## 3. Cross-Version Reconciliation

| Aspect | skills.sh version | GitHub raw (current) |
|--------|------------------|----------------------|
| Aesthetic directions | Explicit list of 11+ named directions | No list; grounds direction in subject's world; names 3 defaults to avoid |
| Pillars framing | "core design pillars" (typography, color/CSS vars, motion, spatial composition, textural details) | "Design principles" (hero as thesis, typography, structure, motion, complexity matching) + token system (color, type, layout, signature) |
| Color | "color theming with CSS variables" | "4–6 named hex values" (CSS vars implied as implementation) |
| Two-pass | Not visible (truncated) | Explicitly detailed: brainstorm token system → critique against brief → build |
| Quality floor | Not visible (truncated) | Explicit: responsive, keyboard focus, reduced motion |
| Signature element | "What's the one thing someone will remember?" (Differentiation) | "the single unique element this page will be remembered by" (token system: Signature) |

The GitHub raw version is a **maturation** of the skills.sh version: less prescriptive about which
aesthetic to pick, more prescriptive about HOW to derive one from the subject, and more detailed about
the process and quality floor.
