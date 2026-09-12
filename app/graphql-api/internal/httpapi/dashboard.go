package httpapi

import (
	"embed"
	"net/http"
)

// The dashboard is embedded in the binary rather than served from a volume or a sidecar:
// the image stays the single deployable unit, which is what lets the same artifact run on
// k3d and EKS with no extra manifest.
//
//go:embed static
var staticFS embed.FS

func (s *Server) handleDashboard(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	b, err := staticFS.ReadFile("static/index.html")
	if err != nil {
		http.Error(w, "dashboard unavailable", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write(b)
}
