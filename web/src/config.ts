// Firebase web configuration for the pol-review app. These values identify the
// project; they are not secrets. Access is controlled by App Check and rules.
export const firebaseConfig = {
  apiKey: "AIzaSyAhZW5Ybafg94SaCnNuCkU3kL-zEawjFk0",
  authDomain: "proof-of-loss.firebaseapp.com",
  projectId: "proof-of-loss",
  storageBucket: "proof-of-loss.firebasestorage.app",
  messagingSenderId: "998086042692",
  appId: "1:998086042692:web:27c6e4d390cc8faf364811",
};

// Cloud model used when the on-device model is not available (Firebase AI Logic
// hybrid inference falls back to it).
export const CLOUD_MODEL = "gemini-3.5-flash-lite";

// Region of the check_claim Cloud Function (functions/main.py).
export const FUNCTIONS_REGION = "us-central1";
