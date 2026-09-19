const AUDIO_EXTENSIONS = ['.mp3', '.wav', '.m4a', '.ogg', '.flac'] as const;
const VIDEO_EXTENSIONS = ['.mp4', '.mov', '.webm', '.m4v'] as const;

// Explicit extensions matter on mobile: some Android/iOS file pickers do not
// expose MP3/M4A (or MOV) files when they receive only an audio/*/video/* MIME
// wildcard. Keep the wildcard too, so recordings with a valid MIME type and a
// device-specific extension remain selectable.
export const AUDIO_FILE_ACCEPT = ['audio/*', ...AUDIO_EXTENSIONS].join(',');
export const VIDEO_FILE_ACCEPT = ['video/*', ...VIDEO_EXTENSIONS].join(',');

function hasExtension(file: File, extensions: readonly string[]) {
  const name = file.name.toLowerCase();
  return extensions.some(extension => name.endsWith(extension));
}

export function isAudioFile(file: File) {
  return file.type.toLowerCase().startsWith('audio/') || hasExtension(file, AUDIO_EXTENSIONS);
}

export function isVideoFile(file: File) {
  return file.type.toLowerCase().startsWith('video/') || hasExtension(file, VIDEO_EXTENSIONS);
}
