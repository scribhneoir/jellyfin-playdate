PYTHON ?= python3
PDC ?= $(PLAYDATE_SDK_PATH)/bin/pdc
SIMULATOR ?= PlaydateSimulator
CLIENT_CONFIG_FILE ?= client/local_config.lua
CLIENT_SOURCE ?= build/client-source
CLIENT_OUTPUT ?= build/Jellyfin
DOTNET_IMAGE ?= mcr.microsoft.com/dotnet/sdk:9.0

.PHONY: all fixture probe package client client-fonts client-package ui-preview configure test serve simulator up down plugin plugin-package plugin-repository configure-plugin plugin-test plugin-repository-test
all: client

build/diagnostic.mkv: tools/make_fixture.py
	$(PYTHON) tools/make_fixture.py $@

build/diagnostic.pds: build/diagnostic.mkv tools/encode.py tools/pds.py
	$(PYTHON) tools/encode.py $< $@

fixture: build/diagnostic.pds
	$(PYTHON) tools/inspect_pds.py $<

probe: build/diagnostic.pds
	mkdir -p build/source
	cp probe/*.lua probe/pdxinfo build/source/
	cp build/diagnostic.pds build/source/fixture.pds
	$(PDC) -sdkpath "$(PLAYDATE_SDK_PATH)" build/source build/PDSProbe.pdx

package: probe
	$(PYTHON) -c 'import shutil; shutil.make_archive("build/PDSProbe", "zip", "build", "PDSProbe.pdx")'

configure:
	$(PYTHON) tools/configure.py

configure-plugin:
	$(PYTHON) tools/configure_plugin.py

plugin:
	mkdir -p build/dotnet-home build/nuget
	docker run --rm --user "$$(id -u):$$(id -g)" -e DOTNET_CLI_HOME=/work/build/dotnet-home -e NUGET_PACKAGES=/work/build/nuget -v "$(CURDIR):/work" -w /work $(DOTNET_IMAGE) dotnet build plugin/Jellyfin.Plugin.Playdate.csproj -c Release -o build/plugin

plugin-package: plugin
	$(PYTHON) tools/package_plugin.py

plugin-repository: plugin
	@test -n "$(REPOSITORY_URL)" || (echo 'Set REPOSITORY_URL to the HTTP(S) directory that will host the plugin ZIP.' >&2; exit 1)
	$(PYTHON) tools/package_plugin.py --repository-url "$(REPOSITORY_URL)"

plugin-test:
	$(PYTHON) tests/plugin_integration.py

plugin-repository-test:
	$(PYTHON) tests/plugin_repository.py

client-fonts:
	mkdir -p client/fonts
	@for file in Roobert-10-Bold.fnt Roobert-10-Bold-table-12-14.png Roobert-20-Medium.fnt Roobert-20-Medium-table-32-32.png; do \
		cp "$(PLAYDATE_SDK_PATH)/Resources/Fonts/Roobert/$$file" "client/fonts/$$file" || exit 1; \
	done

client: client-fonts
	mkdir -p "$(CLIENT_SOURCE)/fonts"
	cp client/main.lua client/ui.lua client/net.lua client/player.lua client/posters.lua client/config.lua client/pdxinfo "$(CLIENT_SOURCE)/"
	cp client/fonts/*.fnt client/fonts/*.png "$(CLIENT_SOURCE)/fonts/"
	if [ -f "$(CLIENT_CONFIG_FILE)" ]; then cp "$(CLIENT_CONFIG_FILE)" "$(CLIENT_SOURCE)/config.lua"; fi
	$(PDC) -sdkpath "$(PLAYDATE_SDK_PATH)" "$(CLIENT_SOURCE)" "$(CLIENT_OUTPUT).pdx"

client-package: client
	$(PYTHON) -c 'import pathlib, shutil, sys; p=pathlib.Path(sys.argv[1]); shutil.make_archive(str(p), "zip", p.parent, p.name+".pdx")' "$(CLIENT_OUTPUT)"

ui-preview: client-fonts
	mkdir -p build/design-preview-source/fonts build/design-preview
	cp client/ui.lua client/net.lua client/player.lua client/posters.lua client/config.lua build/design-preview-source/
	cp client/main.lua build/design-preview-source/app.lua
	cp tests/ui/main.lua build/design-preview-source/main.lua
	cp client/fonts/*.fnt client/fonts/*.png build/design-preview-source/fonts/
	printf 'name=Jellyfin Design Preview\nbundleID=com.scribhneoir.jellyfin-design-preview\nversion=0.1.0\n' > build/design-preview-source/pdxinfo
	$(PDC) -sdkpath "$(PLAYDATE_SDK_PATH)" build/design-preview-source build/DesignPreview.pdx
	$(SIMULATOR) build/DesignPreview.pdx "$(CURDIR)/build/design-preview" > build/design-preview.log 2>&1
	$(PYTHON) -c 'from pathlib import Path; log = Path("build/design-preview.log").read_text(); print(log); assert "UI SMOKE PASSED:" in log, "UI smoke test failed"'

up:
	docker compose --env-file /dev/null up --build -d bridge

down:
	docker compose --env-file /dev/null down

test:
	$(PYTHON) -m unittest discover -s tests -v

serve: build/diagnostic.mkv
	$(PYTHON) tools/serve.py $< --realtime

simulator: probe
	$(SIMULATOR) build/PDSProbe.pdx seconds=16
