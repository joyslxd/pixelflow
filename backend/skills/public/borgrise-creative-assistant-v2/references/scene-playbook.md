# Borgrise Creative Studio Scene Playbook

Use this file as the quality layer above raw API routing. Pick the relevant
scene and adapt its structure before generating.

## 1. White-Background Main Image

Best for:
- ecommerce main image
- marketplace thumbnail
- catalog cover

Prompt ingredients:
- exact product identity
- centered or slightly elevated hero composition
- seamless pure or near-pure white background
- soft studio lighting
- realistic grounding shadow
- clean edges
- no extra props unless explicitly requested

Avoid:
- decorative clutter
- strong color cast
- stylized environment
- floating products without grounding

## 2. Lifestyle Product Image

Best for:
- Xiaohongshu style commerce
- campaign secondary visuals
- detail storytelling

Prompt ingredients:
- exact product identity
- believable environment matched to category
- one primary mood
- natural composition with a clear focal product
- material realism
- light that supports the product's finish

Suggested environment mapping:
- skincare: bathroom counter, vanity, marble sink, bedside
- food/drink: dining table, kitchen island, cafe setting
- tech: desk setup, neutral background, premium studio set
- fashion accessories: wardrobe, mirror zone, editorial shelf

## 3. Poster or Cover Image

Best for:
- ad poster
- campaign KV
- social cover

Prompt ingredients:
- strong focal subject
- obvious negative space for text if needed
- decisive lighting mood
- bold but controlled composition
- brand-consistent palette

Avoid:
- too many independent visual ideas in one frame
- noisy background patterns

## 4. Product Showcase Video

Best for:
- 5-10 second commerce clips
- ad teaser
- feature reveal

Recommended structure:
- opening: immediate product reveal
- middle: one motion pattern
- ending: stable beauty shot or branded finish

Good motion verbs:
- slow orbit
- push-in
- tilt reveal
- hand lift
- cap open
- texture close-up

Avoid:
- multiple unrelated camera ideas in one short clip
- more than one scene change in 10 seconds unless highly intentional

## 5. Image-to-Video

Use when the supplied image must be the exact first frame.

Prompt pattern:
- describe what happens after the first frame
- keep movement simple and elegant
- maintain subject identity
- do not rewrite the whole visual world unless the user wants transformation

Good choices:
- subtle orbit
- slow zoom
- lighting shift
- hand interaction
- reveal of texture or packaging detail

## 6. Reference-Mode Video

Use when the reference is for identity, style, or environment, not exact first
frame locking.

State internally:
- what must stay
- what may change

Examples:
- keep product packaging, change scene
- keep person identity, change background and action
- keep style reference, use a new composition

## 7. Talking-Head or Native-Audio Video

Best for:
- recommendation clips
-口播
- short dialogue ads

Rules:
- one message per clip
- 1-3 short lines for 5-10 seconds
- no dense scriptwriting
- product should be visible early
- dialogue should sound like spoken language, not written copy

Working structures:
- hook + recommendation
- problem + solution
- opinion + proof

## 8. Long Video Planning

Build segment prompts with continuity.

For each segment define:
- scene goal
- shot type
- action
- product message
- transition to next segment

Example 30s structure:

1. 0-10s: hook and reveal
2. 10-20s: demonstration or use scenario
3. 20-30s: finish, brand mood, final beauty shot

Example 60s structure:

1. 0-10s: hook
2. 10-20s: problem context
3. 20-30s: solution reveal
4. 30-40s: product detail
5. 40-50s: proof or lifestyle benefit
6. 50-60s: branded close

## 9. Prompt Compression Rule

If the prompt is getting too long, compress in this order:

1. remove repeated adjectives
2. remove duplicate quality language
3. keep subject identity
4. keep scene
5. keep motion
6. keep selling point
7. keep exclusions

Do not compress away the product identity or the scene objective.
