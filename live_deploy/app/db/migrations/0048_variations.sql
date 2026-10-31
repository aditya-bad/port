-- Variations (app/variations.py): a deployment run beside another (its
-- primary) with different settings, to compare them on the same market.
-- Each is a full deployment of its own; variation_of names its primary
-- (NULL: a primary, or a deployment on its own), variation_label what it
-- tries. A primary deleted leaves its variations on their own.
ALTER TABLE deployments ADD COLUMN IF NOT EXISTS variation_of UUID REFERENCES deployments(id) ON DELETE SET NULL;
ALTER TABLE deployments ADD COLUMN IF NOT EXISTS variation_label TEXT;
CREATE INDEX IF NOT EXISTS deployments_variation_of ON deployments (variation_of) WHERE variation_of IS NOT NULL;
