# Two-hour frame history — 2026-10-26

All captured folders remain for two hours from observation time; then the prior
anomaly plus ten before/after policy and ten-frame buffer apply. The 20 GB global
image cap still takes priority. Previously deleted files cannot be recovered.

39 retention, image-cap and supervisor tests pass in the deployment image.
The broad workspace suite had one unrelated clothing-occlusion failure in
concurrent work (and first required that work's uncopied fixture mounted).
Deployment uses the currently running camera code; only retention code and its
tests were replaced. Those unrelated workspace changes were preserved.
`after.json` verifies deployed retention code and in-place timestamp migration.
`disk-estimate.json` preserves the measured size sample and estimates.
The deployed image is based on the existing app/dependencies; the normal
repository Dockerfile will include this retention change on future builds.
