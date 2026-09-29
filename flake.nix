{
  description = "Native PDS streaming investigation";
  inputs.nixpkgs.url = "flake:nixpkgs";
  outputs = { nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; config.allowUnfree = true; };
    in {
      devShells.${system}.default = pkgs.mkShell {
        packages = [ pkgs.playdate-sdk pkgs.python3 pkgs.ffmpeg pkgs.gnumake ];
        PLAYDATE_SDK_PATH = "${pkgs.playdate-sdk}/share/playdate-sdk";
      };
    };
}
