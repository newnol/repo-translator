from repo_translator.detector import detect_language, has_cjk, count_cjk_chars  # noqa: F401, E402

print("✅ Detector module OK")

from repo_translator.translators import get_translator, list_engines  # noqa: E402

t = get_translator("google", "zh", "en")
print(f"✅ Translator: {t.name}")

engines = list_engines()
print(f"✅ Available engines: {list(engines.keys())}")

from repo_translator.file_filter import get_translatable_files, should_translate  # noqa: F401, E402

print("✅ File filter module OK")

from repo_translator.core import RepoTranslator  # noqa: E402

rt = RepoTranslator(source_lang="zh", target_lang="en", translator_engine="google")
print(f"✅ Core module OK - translator: {rt.translator.name}")

from repo_translator.cli import main  # noqa: F401, E402

print("✅ CLI module OK")

print("\n🎉 All modules loaded successfully!")
