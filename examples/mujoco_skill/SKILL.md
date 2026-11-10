# VLM MuJoCo demo

Use this Skill to inspect and manipulate a simulated blue cube into a green tray without disturbing the red cube.

1. Activate this Skill and inspect live Tool contexts before calls. Query vlm.capabilities and verify the effective model and reasoning_effort against deployment intent before inference; do not assume model selection from tool availability alone.
2. Use sim.observe to obtain an image path, observation ID and capture time. Pass that explicit image to vlm.infer. The inference service does not subscribe to images.
3. Ask vlm.infer for a full task plan, then image grounding and material grasp/destination points in original 960x720 pixels. Use task context mode to retain the goal and explicitly selected records.
4. Ask vlm.infer to produce a JSON plan over the allowed simulation primitives. This is generic JSON output; the external executor validates/interprets it.
5. sim.prepare uses the returned pixels and the matching observation to reconstruct geometry. If the source observation is stale, reobserve and reground.
6. sim.execute accepts only declared primitives. Run pregrasp, descend, close_gripper, test_lift. Observe and use vlm.infer to review before transfer.
7. After corroborated test lift, execute transfer, release, retreat. Observe again and ask vlm.infer to review release and all original task criteria.
8. Model/tool invocation success is not task success. Return unknown when evidence is insufficient. Do not silently substitute oracle positions or assume a held object from gripper opening alone.

Node/service cancellation and robot stopping are separate. Do not call service cancellation as a replacement for cancelling sim.execute. Never expose the simulation's offline evaluation-only route to the model.

Inference tools: vlm.infer, vlm.capabilities. Simulation tools: sim.observe, sim.prepare, sim.execute.
