"""Repository hygiene: versions agree, docs are current, links resolve, scripts are executable."""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tools"), os.path.join(ROOT, "tests")]

from synoSNMP import __version__  # noqa: E402


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class VersionTests(unittest.TestCase):
    def test_version_is_plain_numeric(self):
        # plugin managers (PyPluginStore) only recognise plain numeric tags: no -alpha / -rc suffix
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+(\.\d+)?$")

    def test_plugin_manifest_matches_package(self):
        match = re.search(r'<plugin\b[^>]*\bversion="([^"]+)"', read("plugin.py"))
        self.assertIsNotNone(match, "no version in the plugin manifest")
        self.assertEqual(match.group(1), __version__)

    def test_changelog_top_entry_matches_package(self):
        headings = re.findall(r"^## \[([^\]]+)\]", read("CHANGELOG.md"), re.M)
        released = [h for h in headings if h.lower() != "unreleased"]
        self.assertTrue(released, "CHANGELOG.md has no released version")
        self.assertEqual(released[0], __version__, "newest CHANGELOG entry differs from synoSNMP.__version__")

    def test_readme_status_line_names_the_current_version(self):
        match = re.search(r"^> \*\*Status:\*\* version (\d+\.\d+\.\d+(?:\.\d+)?)", read("README.md"), re.M)
        self.assertIsNotNone(match, "README.md has no '> **Status:** version X.Y.Z' line")
        self.assertEqual(match.group(1), __version__, "README status line differs from synoSNMP.__version__")

    def test_changelog_links_every_release(self):
        text = read("CHANGELOG.md")
        for version in re.findall(r"^## \[(\d[^\]]*)\]", text, re.M):
            self.assertRegex(text, r"(?m)^\[%s\]: https?://" % re.escape(version))


class FileTests(unittest.TestCase):
    REQUIRED = ["LICENSE", "README.md", "CHANGELOG.md", "ROADMAP.md", "SECURITY.md", "CONTRIBUTING.md", "DEPLOY.md",
                "checklist.md", "Makefile", "deploy.sh", ".gitignore", ".github/workflows/tests.yml",
                ".github/workflows/release.yml",
                "docs/DEVICES.md", "docs/SNMP-OIDS.md", "tools/capture_mib.sh", "tools/gen_device_catalog.py"]

    def test_required_files_exist(self):
        missing = [p for p in self.REQUIRED if not os.path.exists(os.path.join(ROOT, p))]
        self.assertEqual(missing, [])

    @unittest.skipIf(os.name != "posix", "POSIX permissions")
    def test_scripts_are_executable(self):
        for path in ("deploy.sh", "tools/capture_mib.sh", "tools/gen_device_catalog.py"):
            self.assertTrue(os.access(os.path.join(ROOT, path), os.X_OK), path + " is not executable")

    def test_markdown_links_resolve(self):
        docs = [p for p in os.listdir(ROOT) if p.endswith(".md")]
        docs += ["docs/" + p for p in os.listdir(os.path.join(ROOT, "docs")) if p.endswith(".md")]
        broken = []
        for doc in docs:
            base = os.path.dirname(os.path.join(ROOT, doc))
            for target in re.findall(r"\]\(([^)\s]+)\)", read(doc)):
                if re.match(r"^(https?:|mailto:|#)", target):
                    continue
                path = target.split("#", 1)[0]
                if path and not os.path.exists(os.path.normpath(os.path.join(base, path))):
                    broken.append("%s -> %s" % (doc, target))
        self.assertEqual(broken, [])

    def test_readme_uses_the_real_directory_name(self):
        readme = read("README.md") + read("DEPLOY.md")
        self.assertIn("Domoticz-Synology-SNMP", readme)
        self.assertNotIn("Domoticz-Synology-Plugin", readme)


class GeneratedDocsTests(unittest.TestCase):
    def test_device_catalogue_is_up_to_date(self):
        import gen_device_catalog
        self.assertEqual(read("docs", "DEVICES.md"), gen_device_catalog.render(),
                         "docs/DEVICES.md is stale: run python3 tools/gen_device_catalog.py")


if __name__ == "__main__":
    unittest.main()
