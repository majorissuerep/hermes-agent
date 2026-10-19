---
sidebar_position: 2.5
title: "Platform Support"
description: "Supported operating systems and installation methods for this fork."
---

# Platform support

This fork targets **Linux and macOS**. On Windows hardware, run it inside a
WSL2 Linux distribution; native Windows, MSIX, and Windows services are not supported.

| Platform | Installation | Updates |
| --- | --- | --- |
| Linux (x86_64, aarch64) | [Source installer](./installation.md) | `hermes update`; rerun the installer for source/runtime repair |
| macOS (Apple Silicon and Intel) | [Source installer](./installation.md) | `hermes update`; rerun the installer for source/runtime repair |
| Linux container | [Build this fork's Dockerfile](../user-guide/docker.md) | Replace the image; retain the encrypted state volume |
| Linux/macOS with Nix | [Flake and modules](./nix-setup.md) | Nix owns the immutable runtime; update the flake |

The local CLI, Ink TUI, and terminal sub-agents share the same agent core.
Tool availability depends on the host, selected tools, and installed extras.

## Qualification

[Install/update acceptance](../developer-guide/reliability.md) uses disposable
containers and native macOS CI machines with isolated homes. Native tests run on
their actual OS, never by pretending a Linux interpreter is macOS. A check on one
architecture does not qualify another architecture by implication.

## Upstream distributions

Nous Research's published desktop DMGs, Docker images, Homebrew/PyPI packages,
and Termux packages are **not this fork**. They do not qualify this fork's vault
or updates. Use the fork's source installer or build this repository. Retained
upstream packaging guides are reference material, not fork release promises.

## Maintenance policy

Do not add native Windows dependencies, installers, services, or release lanes.
When syncing upstream, retain shared portable behavior and omit Windows-only
surface. Frozen compatibility seams needed by old updaters stay until their
migration contract can be retired; they are not supported Windows features.
