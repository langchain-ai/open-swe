Publish a grounded AI assessment for a completed frontend capture on this run's exact pull request head.
First call review_frontend or read_frontend_review and inspect its actual paired screenshot image blocks.
The report stays marked AI review not run until this tool succeeds.
Captures with failed or blocked authentication cannot be assessed.

Write a concise summary of observed changes, useful design strengths, likely regressions, and untested interactions.
Distinguish a visual observation, a subjective design suggestion, and a confirmed behavioral defect.
Pixel changes alone do not establish a defect.
Do not create GitHub findings from pixels without additional behavioral or code evidence.
State the screenshots cover the captured routes and viewports; do not claim untested navigation, responsiveness, keyboard access, or accessibility passed.

annotations_by_scenario maps scenario IDs returned by review_frontend to optional labeled rectangles.
Coordinates refer to original capture dimensions in the page metadata, not the downscaled model thumbnails.
Keep annotations tight, useful, and within image bounds.
Prefix suggestion titles with "Suggestion:" and observed regression titles with "Observed:" so the report distinguishes judgment from evidence.
An empty annotation mapping is valid when the summary suffices.

This publishes the assessment once while preserving all original pixel metrics and screenshots.
The report remains explicit that preview builds are unverified and video is unavailable.
Only assess evidence you actually inspected.
