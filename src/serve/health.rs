use axum::Json;
use shared::Health;

/// `GET /api/health` — liveness probe, always `200 {"status":"ok"}`.
pub async fn handler() -> Json<Health> {
    Json(Health::ok())
}
