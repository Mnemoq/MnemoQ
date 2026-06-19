# Agent Guidelines

## Physics
Collision detection uses AABB broadphase. Always check penetration depth.

## Tooling
Build system uses CMake. Config lives in config.json.

## Performance
Shader pipeline: vertex -> fragment -> compute.
