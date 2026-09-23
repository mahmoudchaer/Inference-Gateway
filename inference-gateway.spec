from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("inference_gateway") + collect_data_files("certifi")

analysis = Analysis(
    ["src/inference_gateway/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on"],
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="inference-gateway",
    console=True,
)
