# File Upload Module

## Purpose

Handle source material intake for Borgrise creative work. This module makes uploaded files usable by later modules.

## Use When

- The user wants to upload videos, images, product files, brand files, scripts, references, or campaign materials.
- The user asks whether files are complete, readable, named clearly, or ready for the next step.
- The workflow needs a clean material inventory before analysis or generation.

## Inputs

- File path, URL, attachment, cloud link, or local material folder.
- Optional campaign context: product, platform, target audience, deadline, output size, language, or market.

## Outputs

- Material list with file names, types, counts, and short notes.
- Missing or unreadable file warnings.
- Suggested normalized naming or grouping.
- Handoff note for `video-analysis` or `creative-generation`.

## Boundary

This module **不做视频分析** and does not write final image/video generation prompts. It only prepares materials and clarifies what is available.

## Checklist

1. Confirm source location and access.
2. Identify file type and intended use.
3. Flag missing, duplicate, unsupported, or low-quality materials.
4. Produce a concise material inventory.
5. State the recommended next module.
