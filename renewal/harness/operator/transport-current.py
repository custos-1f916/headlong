#!/usr/bin/env python3
import pathlib,sys
root=pathlib.Path('/opt/custos/current/renewal')
if not root.is_dir():root=pathlib.Path('/opt/custos/repo/renewal')
sys.path.insert(0,str(root))
from custos_transport import main
raise SystemExit(main())
