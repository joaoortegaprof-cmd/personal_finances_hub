#!/bin/bash
set -e

echo "======================================"
echo "  FinanceHub — Criando pacote .deb"
echo "======================================"

cd "$(dirname "$0")/.."

VERSION="0.1.0"
ARCH="amd64"
PKG="financehub"
PKG_DIR="dist/deb/${PKG}_${VERSION}_${ARCH}"

if [ ! -f "dist/FinanceHub/FinanceHub" ]; then
    echo "ERRO: execute scripts/build.sh primeiro"
    exit 1
fi

echo "Criando estrutura do pacote..."
mkdir -p "${PKG_DIR}/DEBIAN"
mkdir -p "${PKG_DIR}/opt/financehub"
mkdir -p "${PKG_DIR}/usr/local/bin"
mkdir -p "${PKG_DIR}/usr/share/applications"
mkdir -p "${PKG_DIR}/usr/share/icons/hicolor/256x256/apps"

echo "Copiando arquivos..."
cp -r dist/FinanceHub/. "${PKG_DIR}/opt/financehub/"

ln -sf /opt/financehub/FinanceHub \
    "${PKG_DIR}/usr/local/bin/financehub"

if [ -f "frontend/assets/icon.png" ]; then
    cp frontend/assets/icon.png \
        "${PKG_DIR}/usr/share/icons/hicolor/256x256/apps/financehub.png"
fi

cat > "${PKG_DIR}/usr/share/applications/financehub.desktop" << 'DESKTOP'
[Desktop Entry]
Version=1.0
Type=Application
Name=FinanceHub
GenericName=Gestão Financeira
Comment=Controle de patrimônio, investimentos e finanças pessoais
Exec=/opt/financehub/FinanceHub
Icon=financehub
Terminal=false
StartupNotify=true
Categories=Finance;Office;
Keywords=finanças;investimentos;patrimônio;carteira;dividendos;
DESKTOP

cat > "${PKG_DIR}/DEBIAN/postinst" << 'POSTINST'
#!/bin/bash
chmod +x /opt/financehub/FinanceHub
mkdir -p /opt/financehub/data/logs
mkdir -p /opt/financehub/data/backups
update-desktop-database /usr/share/applications/ 2>/dev/null || true
gtk-update-icon-cache /usr/share/icons/hicolor/ 2>/dev/null || true
echo "FinanceHub instalado com sucesso!"
echo "Execute: financehub"
POSTINST
chmod 755 "${PKG_DIR}/DEBIAN/postinst"

cat > "${PKG_DIR}/DEBIAN/prerm" << 'PRERM'
#!/bin/bash
echo "Removendo FinanceHub..."
PRERM
chmod 755 "${PKG_DIR}/DEBIAN/prerm"

INSTALLED_SIZE=$(du -sk "${PKG_DIR}/opt" | cut -f1)
cat > "${PKG_DIR}/DEBIAN/control" << CONTROL
Package: ${PKG}
Version: ${VERSION}
Architecture: ${ARCH}
Maintainer: FinanceHub <financehub@local>
Installed-Size: ${INSTALLED_SIZE}
Depends: libxcb1 (>= 1.11), libx11-6, libglib2.0-0
Recommends: libxcb-icccm4, libxcb-image0, libxcb-keysyms1
Description: Gestão financeira pessoal
 Aplicação desktop completa para controle de patrimônio,
 investimentos, gastos e planejamento financeiro pessoal.
 .
 Funcionalidades:
  - Dashboard com evolução patrimonial
  - Controle de contas, cartões e lançamentos
  - Carteira de investimentos com cotações
  - Simulação financeira e metas pessoais
  - Relatórios em PDF
CONTROL

find "${PKG_DIR}" -type d -exec chmod 755 {} \;
find "${PKG_DIR}" -type f -exec chmod 644 {} \;
chmod 755 "${PKG_DIR}/opt/financehub/FinanceHub"
chmod 755 "${PKG_DIR}/DEBIAN/postinst"
chmod 755 "${PKG_DIR}/DEBIAN/prerm"

echo "Construindo pacote .deb..."
dpkg-deb --build --root-owner-group \
    "${PKG_DIR}" \
    "dist/${PKG}_${VERSION}_${ARCH}.deb"

SIZE=$(du -sh "dist/${PKG}_${VERSION}_${ARCH}.deb" | cut -f1)
echo ""
echo "======================================"
echo "  Pacote .deb criado!"
echo "  Arquivo: dist/${PKG}_${VERSION}_${ARCH}.deb"
echo "  Tamanho: $SIZE"
echo ""
echo "  Para instalar:"
echo "  sudo dpkg -i dist/${PKG}_${VERSION}_${ARCH}.deb"
echo ""
echo "  Para desinstalar:"
echo "  sudo dpkg -r ${PKG}"
echo "======================================"
