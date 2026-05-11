# Cortex REST API Pricing Rates

All rates in USD per 1 million tokens. Source: Snowflake Credit Consumption Table.

## Table 6(b) — REST API with Prompt Caching

### Anthropic Models (AWS)

| Model | Region | Input | Cache Write | Cache Read | Output |
|-------|--------|-------|-------------|------------|--------|
| claude-3-7-sonnet | Regional | 3.30 | 4.13 | 0.33 | 16.50 |
| claude-3-7-sonnet | Global | 3.00 | 3.75 | 0.30 | 15.00 |
| claude-4-opus | Regional | 16.50 | 20.63 | 1.65 | 82.50 |
| claude-4-opus | Global | 15.00 | 18.75 | 1.50 | 75.00 |
| claude-4-sonnet | Regional | 3.30 | 4.13 | 0.33 | 16.50 |
| claude-4-sonnet | Global | 3.00 | 3.75 | 0.30 | 15.00 |
| claude-sonnet-4-5 | Regional | 3.30 | 4.13 | 0.33 | 16.50 |
| claude-sonnet-4-5 | Global | 3.00 | 3.75 | 0.30 | 15.00 |
| claude-sonnet-4-5-long-context | Regional | 6.60 | 8.25 | 0.66 | 24.75 |
| claude-sonnet-4-5-long-context | Global | 6.00 | 7.50 | 0.60 | 22.50 |
| claude-sonnet-4-6 | Regional | 3.30 | 4.13 | 0.33 | 16.50 |
| claude-sonnet-4-6 | Global | 3.00 | 3.75 | 0.30 | 15.00 |
| claude-haiku-4-5 | Regional | 1.10 | 1.38 | 0.11 | 5.50 |
| claude-haiku-4-5 | Global | 1.00 | 1.25 | 0.10 | 5.00 |
| claude-opus-4-5 | Regional | 16.50 | 20.63 | 1.65 | 82.50 |
| claude-opus-4-5 | Global | 15.00 | 18.75 | 1.50 | 75.00 |
| claude-opus-4-6 | Regional | 16.50 | 20.63 | 1.65 | 82.50 |
| claude-opus-4-6 | Global | 15.00 | 18.75 | 1.50 | 75.00 |

### OpenAI Models (AWS)

| Model | Region | Input | Cache Write | Cache Read | Output |
|-------|--------|-------|-------------|------------|--------|
| openai-gpt-5 | AWS Global | 1.25 | 1.25 | 0.13 | 10.00 |
| openai-gpt-5.2 | AWS Global | 1.75 | 1.75 | 0.18 | 14.00 |
| openai-gpt-5.4 | AWS Global | 2.50 | 2.50 | 0.25 | 15.00 |
| openai-gpt-4.1 | AWS Global | 2.00 | 2.00 | 0.50 | 8.00 |

### OpenAI Models (Azure)

| Model | Region | Input | Cache Write | Cache Read | Output |
|-------|--------|-------|-------------|------------|--------|
| openai-gpt-5 | Azure Global | 1.25 | 1.25 | 0.13 | 10.00 |
| openai-gpt-5.2 | Azure Global | 1.75 | 1.75 | 0.18 | 14.00 |
| openai-gpt-5.4 | Azure Global | 2.50 | 2.50 | 0.25 | 15.00 |
| openai-gpt-4.1 | Azure Global | 2.00 | 2.00 | 0.50 | 8.00 |

### Anthropic Models (Azure)

| Model | Region | Input | Cache Write | Cache Read | Output |
|-------|--------|-------|-------------|------------|--------|
| claude-sonnet-4-5 | Azure Regional | 3.30 | 4.13 | 0.33 | 16.50 |
| claude-sonnet-4-5 | Azure Global | 3.00 | 3.75 | 0.30 | 15.00 |
| claude-sonnet-4-6 | Azure Regional | 3.30 | 4.13 | 0.33 | 16.50 |
| claude-sonnet-4-6 | Azure Global | 3.00 | 3.75 | 0.30 | 15.00 |
| claude-haiku-4-5 | Azure Regional | 1.10 | 1.38 | 0.11 | 5.50 |
| claude-haiku-4-5 | Azure Global | 1.00 | 1.25 | 0.10 | 5.00 |

## Table 6(c) — REST API without Prompt Caching

| Model | Input | Output |
|-------|-------|--------|
| deepseek-r1 | 1.35 | 5.40 |
| mistral-large2 | 2.00 | 6.00 |
| llama3.3-70b | 0.72 | 0.72 |
| llama4-maverick | 0.24 | 0.97 |
| snowflake-llama-3.3-70b | 0.72 | 0.72 |

## Pricing Patterns

- Regional = 1.1 × Global (10% premium)
- Cache Write = ~1.25 × Input rate
- Cache Read = ~0.1 × Input rate (90% savings)
- Opus-tier models: ~5× Sonnet-tier pricing
- Haiku-tier models: ~0.33× Sonnet-tier pricing
