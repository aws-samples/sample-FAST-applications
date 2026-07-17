// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Single source of truth for the user-selectable chat LLMs.
 *
 * Both sides of the app derive their model list from SELECTABLE_MODELS here, so
 * the frontend picker options and the backend resolver can never drift apart:
 *  - The backend (Runtime / models.py) gets a "logical key -> physical id +
 *    provider" map via the MODEL_MAP env var (see modelMapJson) and a default
 *    logical key via DEFAULT_MODEL_KEY (see defaultModelKey).
 *  - The frontend gets a key/label list via a CfnOutput that deploy-frontend.py
 *    writes into aws-exports.json (see selectableModelsForFrontend). The
 *    physical id and provider are intentionally excluded so they never reach the
 *    client.
 *
 * Every model in this list is selectable and must actually work; there is no
 * "available" flag. The frontend sends only the logical `key` (e.g. "opus-4.8")
 * in the invoke payload; the backend resolves it to the physical `id`. This
 * hides Bedrock implementation details (inference-profile prefix, version
 * suffix, routing strategy, endpoint kind) from the client and lets a model
 * be upgraded by changing its `id` here while the stable `key` keeps
 * localStorage selections valid.
 */

/** A model the user can pick in the UI, plus how the backend invokes it. */
export interface SelectableModel {
	/**
	 * Stable logical key exchanged between FE and BE (e.g. "opus-4.8"). Chosen to
	 * include the generation so it maps clearly to the label. Keep this stable
	 * across version bumps (change `id` instead) so persisted selections survive.
	 */
	key: string;
	/** Human-facing display name shown in the picker (e.g. "Claude Opus 4.8"). */
	label: string;
	/**
	 * Physical Bedrock model id / inference-profile id the backend invokes. Kept
	 * server-side (never emitted to the frontend). Copied verbatim from the model
	 * card / `aws bedrock list-inference-profiles` output.
	 */
	id: string;
	/** Which Strands model class / auth path the backend uses for this model. */
	provider: "anthropic" | "openai";
	/** Exactly one entry should set this; it becomes DEFAULT_MODEL_KEY. */
	default?: boolean;
}

/**
 * The selectable models.
 *
 * Claude entries use Global cross-region inference profiles (`global.` prefix),
 * verified ACTIVE in us-east-1 in the account this template was validated
 * against. Global routing reaches any supported region, which also covers
 * Sonnet 4.6 (no in-region availability in us-east-1). Opus 4.8 / Sonnet 4.6 /
 * Fable 5 use the short id form with no date and no `-v1:0`; Haiku 4.5 keeps
 * the dated long form. Fable 5 additionally requires the account data
 * retention mode `provider_data_share` in the calling region — see its entry.
 *
 * OpenAI GPT-5.x are served via the bedrock-mantle / OpenAI Responses API (a
 * different Strands class than Claude). Since 2026-06 the models are available
 * in us-east-1, reached from the closed network through the in-region
 * bedrock-mantle VPC endpoint that VpcStack always provisions.
 * GPT-5.5 has a known output-duplication bug on its endpoint but is offered anyway.
 */
export const SELECTABLE_MODELS: readonly SelectableModel[] = [
	{
		key: "fable-5",
		label: "Claude Fable 5",
		// Fable 5 requires data retention mode `provider_data_share` (its
		// allowed_modes is exactly that; `default` is rejected with "data
		// retention mode 'default' is not available for this model"). The mode is
		// evaluated against the account setting of the REGION THAT RECEIVES THE
		// REQUEST (the bedrock-runtime endpoint the caller uses — us-east-1 for
		// this runtime), not the regions global routing lands in (verified
		// empirically 2026-06-10: success tracks the source region's setting
		// 100%). PUT /data-retention {"mode":"provider_data_share"} on us-east-1
		// is the operational prerequisite. Bedrock uses the bare
		// profile id; the `[1m]` suffix from first-party API contexts is
		// rejected.
		id: "global.anthropic.claude-fable-5",
		provider: "anthropic",
	},
	{
		key: "opus-4.8",
		label: "Claude Opus 4.8",
		id: "global.anthropic.claude-opus-4-8",
		provider: "anthropic",
	},
	{
		key: "sonnet-5",
		label: "Claude Sonnet 5",
		// Uses the `global.` cross-region profile like the other Claude models.
		// No data-retention requirement (unlike Fable 5) and no `-v1:0`/date
		// suffix (short id form, same as Opus 4.8 / Sonnet 4.6). Verified via
		// Converse in us-east-1 (global. and us. profiles both ACTIVE and
		// returning end_turn).
		id: "global.anthropic.claude-sonnet-5",
		provider: "anthropic",
	},
	{
		key: "sonnet-4.6",
		label: "Claude Sonnet 4.6",
		id: "global.anthropic.claude-sonnet-4-6",
		provider: "anthropic",
		default: true,
	},
	{
		key: "haiku-4.5",
		label: "Claude Haiku 4.5",
		id: "global.anthropic.claude-haiku-4-5-20251001-v1:0",
		provider: "anthropic",
	},
	{
		key: "gpt-5.5",
		label: "OpenAI GPT-5.5",
		id: "openai.gpt-5.5",
		provider: "openai",
	},
	{
		key: "gpt-5.4",
		label: "OpenAI GPT-5.4",
		id: "openai.gpt-5.4",
		provider: "openai",
	},
];

/** One entry of the backend resolution map (logical key -> physical model). */
interface ModelMapEntry {
	id: string;
	provider: SelectableModel["provider"];
}

/**
 * Build the backend "logical key -> { id, provider }" map as a JSON string for
 * the MODEL_MAP env var. Every model is included (there is no availability
 * gate).
 *
 * @returns A JSON object string keyed by logical model key.
 */
export function modelMapJson(): string {
	const map: { [key: string]: ModelMapEntry } = {};
	for (const model of SELECTABLE_MODELS) {
		map[model.key] = {
			id: model.id,
			provider: model.provider,
		};
	}
	return JSON.stringify(map);
}

/**
 * Resolve the default model's logical key for the DEFAULT_MODEL_KEY env var.
 * Fails loudly (no silent fallback) if the registry is misconfigured, so a
 * missing/duplicate default is caught at synth time rather than at runtime.
 *
 * @returns The logical key of the single model marked default:true.
 * @throws Error if zero or more than one model is marked default.
 */
export function defaultModelKey(): string {
	const defaults = SELECTABLE_MODELS.filter((m) => m.default);
	if (defaults.length !== 1) {
		throw new Error(
			`SELECTABLE_MODELS must have exactly one default model, found ${defaults.length}`,
		);
	}
	return defaults[0].key;
}

/** The frontend-facing view of a model (no physical id, no provider). */
interface FrontendModel {
	key: string;
	label: string;
	/** Present and true only on the default model, so the picker can pre-select it. */
	default?: boolean;
}

/**
 * Build the frontend picker list as a JSON string for the SelectableModelsJson
 * CfnOutput. Deliberately exposes only key/label/default; the physical id and
 * provider stay server-side. The default flag lets the picker pre-select the
 * same model the backend uses when no key is sent, so the displayed and actual
 * defaults agree.
 *
 * @returns A JSON array string of { key, label, default? }.
 */
export function selectableModelsForFrontend(): string {
	const list: FrontendModel[] = SELECTABLE_MODELS.map((m) => ({
		key: m.key,
		label: m.label,
		...(m.default ? { default: true } : {}),
	}));
	return JSON.stringify(list);
}
