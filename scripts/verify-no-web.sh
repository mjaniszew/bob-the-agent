#!/bin/bash
# verify-no-web.sh - Verify web interface has been completely removed
# Exit 0 if verification passes, 1 if fails

set -e

echo "=== Verifying Web Interface Removal ==="

# Check 1: Frontend directory should not exist
if [ -d "src/frontend" ]; then
    echo "FAIL: src/frontend directory still exists"
    exit 1
fi
echo "✓ src/frontend removed"

# Check 2: API directory should not exist
if [ -d "src/api" ]; then
    echo "FAIL: src/api directory still exists"
    exit 1
fi
echo "✓ src/api removed"

# Check 3: Web config directory should not exist
if [ -d "src/config/web" ]; then
    echo "FAIL: src/config/web directory still exists"
    exit 1
fi
echo "✓ src/config/web removed"

# Check 4: Web Dockerfile should not exist
if [ -f "dockerfiles/Dockerfile.web" ]; then
    echo "FAIL: dockerfiles/Dockerfile.web still exists"
    exit 1
fi
echo "✓ dockerfiles/Dockerfile.web removed"

# Check 5: compose.yaml should not reference 'web' service
if grep -q "web:" compose.yaml; then
    echo "FAIL: compose.yaml still contains 'web' service"
    exit 1
fi
echo "✓ compose.yaml has no 'web' service"

# Check 6: compose.yaml should be valid YAML
if ! docker compose config > /dev/null 2>&1; then
    echo "FAIL: compose.yaml is not valid"
    exit 1
fi
echo "✓ compose.yaml is valid"

# Check 7: GENERAL.md should not have Web Interface section
if grep -q "## Web Interface" docs/features/GENERAL.md; then
    echo "FAIL: docs/features/GENERAL.md still has Web Interface section"
    exit 1
fi
echo "✓ docs/features/GENERAL.md has no Web Interface section"

# Check 8: CONFIGURATION.md should not have Web Interface section
if grep -q "### Web Interface" docs/CONFIGURATION.md; then
    echo "FAIL: docs/CONFIGURATION.md still has Web Interface section"
    exit 1
fi
echo "✓ docs/CONFIGURATION.md has no Web Interface section"

# Check 9: INSTALLATION.md should not have Web Interface references
if grep -q "WEB_USERNAME\|WEB_PASSWORD\|web interface" docs/INSTALLATION.md; then
    echo "FAIL: docs/INSTALLATION.md still has Web Interface references"
    exit 1
fi
echo "✓ docs/INSTALLATION.md has no Web Interface references"

# Check 10: TROUBLESHOOTING.md should not have Web Interface section
if grep -q "### Web Interface Issues" docs/TROUBLESHOOTING.md; then
    echo "FAIL: docs/TROUBLESHOOTING.md still has Web Interface Issues section"
    exit 1
fi
echo "✓ docs/TROUBLESHOOTING.md has no Web Interface Issues section"

# Check 11: ARCHITECTURE.md should not reference web service
if grep -q "│   web   │\|│  :8080  │\|│  nginx  │" docs/ARCHITECTURE.md; then
    echo "FAIL: docs/ARCHITECTURE.md still references web service"
    exit 1
fi
echo "✓ docs/ARCHITECTURE.md has no web service references"

echo ""
echo "=== All Verification Checks Passed ==="
exit 0