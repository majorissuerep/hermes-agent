---
sidebar_position: 4
title: Local Model Endpoints
description: Connect Hermes to a local OpenAI-compatible inference server.
---

# Local Model Endpoints

Hermes can use a local inference server through the supported **Custom endpoint**
OpenAI-compatible transport. Start and manage the server yourself; there is no
built-in local-model provider wizard or desktop settings panel.

## Connect a server

1. Start your chosen server with an OpenAI-compatible API and load a model.
2. Run `hermes model` and choose **Custom endpoint**.
3. Enter its API URL, for example `http://127.0.0.1:8080/v1`.
4. Enter the server's API key if it requires one; otherwise leave the optional
   key empty. Select or enter the exact model ID served by that endpoint.
5. Start Hermes and send a short message to verify the route.

The logical model configuration uses `provider: custom`, the endpoint's
`base_url`, and its model ID. Use the setup/configuration commands to update
Hermes-managed encrypted settings; do not replace ciphertext with a plaintext
configuration file. Saved named custom providers also work with `/model`.

A loopback URL reaches the machine running Hermes. If Hermes runs inside a
container, the inference server must be reachable from that container.

Older homes may retain `local_runtime.enabled`, `local_runtime.backend` and
`local_runtime.detect_ports`. These legacy settings do not enable a bundled
local-provider wizard; configure the server's explicit custom endpoint instead.

## Server guides

- [llama.cpp or MLX on macOS](../guides/local-llm-on-mac.md)
- [Ollama](../guides/local-ollama-setup.md)

Choose a model with tool-calling support for agent workflows and enough context
for the conversation. Model downloads, runtime updates, memory limits, and server
lifetime remain the server operator's responsibility. Local inference does not
make separately configured web, browser, or cloud tools offline.
