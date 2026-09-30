{
  description = "Native PDS streaming investigation";
  inputs.nixpkgs.url = "flake:nixpkgs";
  outputs = { nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; config.allowUnfree = true; };
      playdateSdk = pkgs.playdate-sdk.overrideAttrs (old: {
        version = "3.1.2";
        src = pkgs.fetchurl {
          url = "https://download.panic.com/playdate_sdk/Linux/PlaydateSDK-3.1.2.tar.gz";
          sha256 = "7e3d912611e82f79007afd2a3a9e68a19c8bfc7025b5f761805f21368470d793";
        };
        installPhase = builtins.replaceStrings [ old.version ] [ "3.1.2" ] old.installPhase;
      });
    in {
      devShells.${system}.default = pkgs.mkShell {
        packages = [ playdateSdk pkgs.python3 pkgs.ffmpeg pkgs.gnumake ];
        PLAYDATE_SDK_PATH = "${playdateSdk}/share/playdate-sdk";
      };
    };
}
