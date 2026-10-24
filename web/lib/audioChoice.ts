// Which mic and speaker a voice session uses. Pure, so the default is testable.

export type AudioChoice = "device" | "laptop";

export const AUDIO_CHOICE_KEY = "kravein.voice.audio";

export function parseChoice(value: unknown): AudioChoice | null {
  return value === "device" || value === "laptop" ? value : null;
}

/**
 * An explicit earlier choice wins. Otherwise a page that is not on the laptop
 * itself (the phone app, another device) uses its own mic and speaker, and the
 * laptop's page uses the laptop's. `localClient` comes from the server, which
 * can see where the connection really comes from; unknown means "assume the
 * laptop", the behaviour that existed before this choice did.
 */
export function defaultAudioChoice(
  localClient: boolean | undefined,
  stored: unknown,
): AudioChoice {
  return parseChoice(stored) ?? (localClient === false ? "device" : "laptop");
}
