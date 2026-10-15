"""Migration tombstones, never a provider registry or an inference route.

Old session rows can retain the previous provider's URL after a switch.
Retiring that provider must not make its canonical URL look like a custom proxy.
Keep exact endpoints only: unknown operator-owned relays remain valid.
"""

RETIRED_INFERENCE_ENDPOINTS = frozenset({
    "https://inference-api.nousresearch.com/v1",
    "https://openrouter.ai/api/v1",
    "https://portal.qwen.ai/v1",
    "https://api.githubcopilot.com",
    "acp://copilot",
    "http://127.0.0.1:1234/v1",
    "https://generativelanguage.googleapis.com/v1beta",
    "https://api.moonshot.ai/v1",
    "https://api.moonshot.cn/v1",
    "https://api.kimi.com/coding",
    "https://api.kimi.com/coding/v1",
    "https://api.stepfun.ai/step_plan/v1",
    "https://api.arcee.ai/api/v1",
    "https://api.gmi-serving.com/v1",
    "https://api.actual.inc/v1",
    "https://api.minimax.io/anthropic",
    "https://api.minimaxi.com/anthropic",
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "https://coding-intl.dashscope.aliyuncs.com/v1",
    "https://coding.dashscope.aliyuncs.com/v1",
    "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
    "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    "https://api.deepseek.com/v1",
    "https://integrate.api.nvidia.com/v1",
    "https://opencode.ai/zen/v1",
    "https://opencode.ai/zen/go/v1",
    "https://api.kilo.ai/api/gateway",
    "https://router.huggingface.co/v1",
    "https://api.xiaomimimo.com/v1",
    "https://tokenhub.tencentmaas.com/v1",
    "https://api.lkeap.cloud.tencent.com/plan/anthropic",
    "https://ollama.com/v1",
    "https://bedrock-runtime.us-east-1.amazonaws.com",
    "https://aiplatform.googleapis.com",
    "https://api.deepinfra.com/v1/openai",
    "https://api.fireworks.ai/inference/v1",
    "https://api.tokenfactory.nebius.com/v1",
    "https://api.novita.ai/openai/v1",
    "https://api.upstage.ai/v1",
})
