-- Register Shopify as a first-class site connector.
ALTER TABLE seo_agent.sites
  DROP CONSTRAINT IF EXISTS sites_site_type_check;

ALTER TABLE seo_agent.sites
  ADD CONSTRAINT sites_site_type_check
  CHECK (site_type IN ('main', 'wp', 'blog', 'shopify', 'other'));
