// Firebase setup: the app, App Check, Firebase AI Logic and the check_claim
// Cloud Function. No analytics: nothing about a claim leaves for tracking.

import { initializeApp } from "firebase/app";
import { initializeAppCheck, ReCaptchaEnterpriseProvider, type AppCheck } from "firebase/app-check";
import { getAI, GoogleAIBackend } from "firebase/ai";
import { getFunctions, httpsCallable } from "firebase/functions";
import { firebaseConfig, FUNCTIONS_REGION } from "./config";

export const app = initializeApp(firebaseConfig);

declare global {
  // eslint-disable-next-line no-var
  var FIREBASE_APPCHECK_DEBUG_TOKEN: boolean | string | undefined;
}

// App Check. On the dev server the SDK prints a debug token to the browser
// console once; register it in the Firebase console (App Check, Apps, web app,
// Manage debug tokens). In a deployed build the reCAPTCHA Enterprise key is used.
export let appCheck: AppCheck | null = null;
export let appCheckNote = "";
const siteKey = import.meta.env.VITE_RECAPTCHA_SITE_KEY as string | undefined;
if (import.meta.env.DEV) self.FIREBASE_APPCHECK_DEBUG_TOKEN = true;
if (siteKey || import.meta.env.DEV) {
  appCheck = initializeAppCheck(app, {
    provider: new ReCaptchaEnterpriseProvider(siteKey || "debug-only"),
    isTokenAutoRefreshEnabled: true,
  });
  appCheckNote = siteKey ? "App Check on" : "App Check debug token";
} else {
  appCheckNote = "App Check not configured: set VITE_RECAPTCHA_SITE_KEY";
}

export const ai = getAI(app, { backend: new GoogleAIBackend() });

const functions = getFunctions(app, FUNCTIONS_REGION);
export const checkClaim = httpsCallable<Record<string, unknown>, CheckResult>(functions, "check_claim");

export interface LineResult {
  status: string;
  reason: string;
  line_id: string | null;
  sighting_id: string | null;
  room: string;
  differences: string[];
  needs_attention: boolean;
  second_look: string;
  review_status: string;
}

export interface Valuation {
  line_id: string;
  quantity: number;
  replacement_cost: number | null;
  price_basis: string;
  price_sources: { source: string; url: string; price: number; title: string }[];
  age_years: number | null;
  depreciation_pct: number | null;
  actual_cash_value: number | null;
  recoverable_depreciation: number | null;
  depreciation_explained: string;
  claimed_total: number | null;
  documented_total: number | null;
  gap: number | null;
  flags: string[];
}

export interface CheckResult {
  as_of: string;
  loss_type: string;
  results: LineResult[];
  valuations: Record<string, Valuation>;
  like_kind_and_quality: { line_id: string; exceeds: boolean; explained: string }[];
  documented_value: {
    assetValue: number;
    actualCashValue: number;
    linesCounted: number;
    linesUnpriced: number;
    linesExcluded: Record<string, number>;
  };
  counts: Record<string, number>;
  needs_attention: number;
}
