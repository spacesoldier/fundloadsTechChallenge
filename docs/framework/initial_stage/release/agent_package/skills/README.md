# Ringo Skill Templates (Application-Focused)

These are copy-ready `SKILL.md` templates for agent workflows in repositories that **use** the framework.

## Included Skills

- `feature-to-pr/`
- `decompose-feature-to-runtime-design/`
- `scaffold-node-service-store/`
- `scaffold-port-and-adapter/`
- `map-config-and-process-groups/`
- `build-e2e-pipeline-test/`

## Install Into Target Repo

```bash
mkdir -p .agents/skills
cp -R docs/framework/initial_stage/release/agent_package/skills/* .agents/skills/
```

Then adapt each `SKILL.md` to local paths/commands.
