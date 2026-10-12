<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { defaultTheme, themes, themeStorageKey, validTheme, type ThemeId } from "../lib/themes.ts";

const selected = ref<ThemeId>(defaultTheme);
const ready = ref(false);

function apply(value: unknown, persist = false) {
  selected.value = validTheme(value);
  document.documentElement.dataset.theme = selected.value;
  const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (meta) meta.content = getComputedStyle(document.documentElement).getPropertyValue("--theme-paper").trim();
  if (persist) {
    try { localStorage.setItem(themeStorageKey, selected.value); } catch { /* Page remains switchable. */ }
  }
}
function change(event: Event) {
  apply((event.target as HTMLSelectElement).value, true);
}
function sync(event: StorageEvent) {
  if (event.key === themeStorageKey || event.key === null) apply(event.newValue);
}
onMounted(() => {
  apply(document.documentElement.dataset.theme);
  ready.value = true;
  window.addEventListener("storage", sync);
});
onUnmounted(() => window.removeEventListener("storage", sync));
</script>

<template>
  <label class="theme-controls" for="site-theme">
    <span class="theme-label">主题</span>
    <select id="site-theme" class="theme-picker" name="theme" :value="selected" :disabled="!ready" @change="change">
      <option v-for="theme in themes" :key="theme.id" :value="theme.id">{{ theme.name }}</option>
    </select>
  </label>
</template>
