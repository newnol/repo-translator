from repo_translator.detector import detect_language, has_cjk, count_cjk_chars
print("✅ Detector module OK")

from repo_translator.translators import get_translator, list_engines
t = get_translator('google', 'zh', 'en')
print(f"✅ Translator: {t.name}")

engines = list_engines()
print(f"✅ Available engines: {list(engines.keys())}")

from repo_translator.file_filter import get_translatable_files, should_translate
print("✅ File filter module OK")

from repo_translator.core import RepoTranslator
rt = RepoTranslator(source_lang='zh', target_lang='en', translator_engine='google')
print(f"✅ Core module OK - translator: {rt.translator.name}")

from repo_translator.cli import main
print("✅ CLI module OK")

print("\n🎉 All modules loaded successfully!")
