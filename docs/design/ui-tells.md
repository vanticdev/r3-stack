# Light-theme review checklist

These are review heuristics, not evidence that software was AI generated. Count
one occurrence per tell per page, not one per element. Real engineering data,
status semantics and necessary containment take precedence over stylistic rules.

| ID | Checkable tell | Source |
| --- | --- | --- |
| C | Secondary text or status text fails 4.5:1 against its surface | [WCAG contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html) |
| H | Page and section headings have insufficient distinction; no clear entry point | [NN/g visual hierarchy](https://www.nngroup.com/articles/visual-hierarchy-ux-definition/) |
| B | Repeated rounded boxes give unrelated information equal emphasis | [Mowgli UI tells](https://mowgli.ai/blog/why-ai-built-apps-look-the-same) |
| P | Repeated pill backgrounds compete with the actual result text | [Identity Forge](https://identityforge.io/learn/make-ai-ui-less-generic) |
| F | Placeholder-only forms, clipped content or inconsistent page gutters look unfinished | [NN/g complex applications](https://www.nngroup.com/articles/complex-application-design/) |
| E | Large centered empty states detach guidance from the working area | [AI slop pattern catalog](https://github.com/borghei/Claude-Skills/blob/main/engineering/design-auditor/references/ai_slop_patterns.md) |
| G | Decorative purple gradients, universal shadows, emoji icons or generic hero copy | [Mowgli UI tells](https://mowgli.ai/blog/why-ai-built-apps-look-the-same) |

Round 1 queries: “AI generated UI tells rounded cards gradients generic dashboard”;
“Nielsen Norman dashboard visual hierarchy information density”. G was absent.
Typography is judged by function and legibility, not by whether a popular font is installed.

Round 2 queries: “site.nngroup.com form placeholders labels fields”; “AI interface
generic design empty states excessive cards dashboard”. Added explicit checks:
fields retain visible labels while filled; model rows fit the available column;
empty-state guidance aligns with its section. Sources:
[NN/g placeholders](https://www.nngroup.com/articles/form-design-placeholders/),
[W3C forms](https://design-system.w3.org/styles/forms.html),
[Home Assistant empty states](https://contributors.home-assistant.io/design/patterns/empty-states/).
