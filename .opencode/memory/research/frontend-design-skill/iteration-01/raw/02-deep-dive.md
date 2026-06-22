# Deep Dive — SKILL.md content and design framework

## Sources and tools used
- webfetch of https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/SKILL.md — full skill instructions.
- webfetch of https://www.skills.sh/anthropics/skills/frontend-design — public summary and installation line.
- webfetch of https://github.com/anthropics/skills/commits/main/skills/frontend-design/SKILL.md — recent commit history.

## Skill metadata
`yaml
---
name: frontend-design
description: Guidance for distinctive, intentional visual design when building new UI or reshaping an existing one. Helps with aesthetic direction, typography, and making choices that don't read as templated defaults.
license: Complete terms in LICENSE.txt
---
`

## Core framing: design lead at a small studio
The skill asks the model to act as a design lead at a small studio whose clients reject templated work. The mandate is to make deliberate, opinionated choices and take one real aesthetic risk that can be justified.

## Ground it in the subject
Before designing, the model must pin down:
1. **Subject** — one concrete subject.
2. **Audience** — who uses it.
3. **Page's single job** — what the interface must do.
4. **Human preferences / memory** — reuse any prior context about the user's taste or previous designs.
5. **Materials and vernacular** — draw from the subject's own world for distinctive choices.

## Design principles
1. **The hero is a thesis.** The hero should open with the most characteristic thing in the subject's world, not default to a big number + gradient accent.
2. **Typography carries personality.** Deliberate display/body pairings, a clear type scale, intentional weights, widths, and spacing.
3. **Structure is information.** Numbered markers, dividers, labels, eyebrows must encode real content meaning; they should not be decorative.
4. **Leverage motion deliberately.** Page-load sequences, scroll-triggered reveals, hover micro-interactions, ambient atmosphere — but only if they serve the subject. Extra animation can itself feel AI-generated.
5. **Match complexity to the vision.** Maximalist needs elaboration; minimal needs precision.
6. **Copy is design material.** Avoid templated copy. Use active voice, plain verbs, sentence case, consistent vocabulary. Errors explain what happened and how to fix it.

## Aesthetic directions explicitly mentioned
- Brutally minimal
- Maximalist chaos
- Retro-futuristic
- Organic / natural
- Luxury / refined
- Playful / toy-like
- Editorial / magazine
- Brutalist / raw
- Art deco / geometric
- Soft / pastel
- Industrial / utilitarian

## Two-pass process: brainstorm > plan > critique > build > critique again
### Pass 1 — Design plan (compact token system)
- **Color**: 4–6 named hex values.
- **Type**: 2+ roles (characterful display face, complementary body face, optional utility face).
- **Layout**: one-sentence prose + ASCII wireframes.
- **Signature**: the single unique element the page will be remembered by.

### Self-critique before building
- Review the plan against a similar prompt to see if the result is generic.
- Revise anything that reads like a default.
- Only after confirming uniqueness should code be written.

### Pass 2 — Build
- Follow the revised plan exactly.
- Derive every color and type decision from the plan.
- Watch CSS specificity conflicts (e.g., .section vs .cta).
- Do planning in thinking; only show the user ideas when confident they will delight.

## Restraint and quality floor
- Spend boldness in one place; keep the surroundings quiet.
- Not taking a risk can itself be a risk.
- Quality floor without announcing it:
  - Responsive down to mobile.
  - Visible keyboard focus.
  - Reduced motion respected.
- Self-critique while building; take screenshots if the environment supports it.
- "Chanel rule": before finishing, remove one accessory.

## Writing-in-design guidance
- Words exist to make understanding easier.
- Write from the user's side of the screen.
- Active voice as default; consistent vocabulary across the flow.
- Treat failure and emptiness as directional moments.
- Conversational register, sentence case, no filler.

## Key takeaways
- The skill is a structured design thinking protocol, not a component library.
- It forces explicit aesthetic decisions before implementation.
- It recognizes and tries to avoid three current AI-generated default looks:
  1. Warm cream background (#F4F1EA) + high-contrast serif + terracotta accent.
  2. Near-black background + single bright acid-green or vermilion accent.
  3. Broadsheet layout with hairline rules, zero border-radius, dense newspaper columns.
- It demands production-grade code quality and accessibility basics.
