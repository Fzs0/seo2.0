"""v2 Rule Engine: 3 张表 (rule_sets / rule_field_map / rule_audit_log) + 2 视图 + trigger + type check。

rev: 149bcf2501fe
depends: fc99b012247e
"""
from alembic import op

revision = "149bcf2501fe"
down_revision = "fc99b012247e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # rule_sets：版本化规则容器
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS seo_agent.rule_sets (
          id                bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          name              varchar(64) NOT NULL,
          version           varchar(32) NOT NULL,
          source            varchar(16) NOT NULL,
          payload           jsonb       NOT NULL,
          is_active         boolean     NOT NULL DEFAULT false,
          effective_at      timestamptz NOT NULL DEFAULT now(),
          created_by        varchar(64),
          notes             text,
          parent_version_id bigint      REFERENCES seo_agent.rule_sets(id) ON DELETE SET NULL,
          created_at        timestamptz NOT NULL DEFAULT now(),
          updated_at        timestamptz NOT NULL DEFAULT now(),

          CONSTRAINT rule_sets_source_chk
            CHECK (source IN ('file','db','api')),
          CONSTRAINT rule_sets_version_chk
            CHECK (version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+([-.+][0-9A-Za-z.-]+)?$'),
          CONSTRAINT rule_sets_payload_object_chk
            CHECK (jsonb_typeof(payload) = 'object'),
          CONSTRAINT rule_sets_id_positive_chk
            CHECK (id > 0)
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS rule_sets_one_active_per_name ON seo_agent.rule_sets (name) WHERE is_active;"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS rule_sets_name_version_uk ON seo_agent.rule_sets (name, version);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS rule_sets_family_active_idx ON seo_agent.rule_sets (name, effective_at DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS rule_sets_payload_gin_idx ON seo_agent.rule_sets USING gin (payload jsonb_path_ops);"
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS set_rule_sets_updated_at ON seo_agent.rule_sets;
        CREATE TRIGGER set_rule_sets_updated_at BEFORE UPDATE ON seo_agent.rule_sets
        FOR EACH ROW EXECUTE FUNCTION seo_agent.set_updated_at();
        """
    )

    # rule_field_map
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS seo_agent.rule_field_map (
          id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          rule_set_id   bigint      NOT NULL REFERENCES seo_agent.rule_sets(id) ON DELETE CASCADE,
          rule_layer    varchar(32) NOT NULL,
          rule_key      varchar(128) NOT NULL,
          display_name  varchar(128) NOT NULL,
          value_type    varchar(16) NOT NULL,
          default_value jsonb,
          source_file   varchar(256) NOT NULL,
          source_line   integer,
          is_migrated   boolean     NOT NULL DEFAULT false,
          notes         text,
          created_at    timestamptz NOT NULL DEFAULT now(),
          updated_at    timestamptz NOT NULL DEFAULT now(),

          CONSTRAINT rule_field_map_layer_chk
            CHECK (rule_layer IN ('classifier','scorer','content_plan','assets','locale','workflow','output_format','references','anchor','images','global_plan')),
          CONSTRAINT rule_field_map_type_chk
            CHECK (value_type IN ('number','string','boolean','array','object')),
          CONSTRAINT rule_field_map_key_chk
            CHECK (rule_key = lower(rule_key) AND rule_key ~ '^[a-z0-9_.]+$'),
          CONSTRAINT rule_field_map_line_chk
            CHECK (source_line IS NULL OR source_line > 0),
          CONSTRAINT rule_field_map_id_positive_chk
            CHECK (id > 0)
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS rule_field_map_set_layer_key_uk ON seo_agent.rule_field_map (rule_set_id, rule_layer, rule_key);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS rule_field_map_unmigrated_idx ON seo_agent.rule_field_map (rule_layer, is_migrated);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS rule_field_map_key_idx ON seo_agent.rule_field_map (rule_layer, rule_key);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS rule_field_map_default_gin_idx ON seo_agent.rule_field_map USING gin (default_value jsonb_path_ops);"
    )

    # default_value 类型守卫 trigger
    op.execute(
        """
        CREATE OR REPLACE FUNCTION seo_agent.check_default_value_type()
        RETURNS trigger AS $$
        DECLARE
          got text;
        BEGIN
          IF NEW.default_value IS NULL THEN
            RETURN NEW;
          END IF;
          got := jsonb_typeof(NEW.default_value);
          IF (NEW.value_type = 'number'  AND got <> 'number')
          OR (NEW.value_type = 'string'  AND got <> 'string')
          OR (NEW.value_type = 'boolean' AND got <> 'boolean')
          OR (NEW.value_type = 'array'   AND got <> 'array')
          OR (NEW.value_type = 'object'  AND got <> 'object') THEN
            RAISE EXCEPTION 'rule_field_map.default_value jsonb type (%) does not match value_type (%)',
                            got, NEW.value_type;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS rule_field_map_default_type_trg ON seo_agent.rule_field_map;
        CREATE TRIGGER rule_field_map_default_type_trg
        BEFORE INSERT OR UPDATE OF default_value, value_type ON seo_agent.rule_field_map
        FOR EACH ROW EXECUTE FUNCTION seo_agent.check_default_value_type();
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS set_rule_field_map_updated_at ON seo_agent.rule_field_map;
        CREATE TRIGGER set_rule_field_map_updated_at BEFORE UPDATE ON seo_agent.rule_field_map
        FOR EACH ROW EXECUTE FUNCTION seo_agent.set_updated_at();
        """
    )

    # rule_audit_log
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS seo_agent.rule_audit_log (
          id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          rule_set_id   bigint      NOT NULL REFERENCES seo_agent.rule_sets(id) ON DELETE RESTRICT,
          action        varchar(16) NOT NULL,
          actor         varchar(64) NOT NULL,
          reason        text,
          snapshot_diff jsonb       NOT NULL DEFAULT '{}'::jsonb,
          created_at    timestamptz NOT NULL DEFAULT now(),

          CONSTRAINT rule_audit_action_chk
            CHECK (action IN ('activate','deactivate','create','rollback','supersede')),
          CONSTRAINT rule_audit_id_positive_chk
            CHECK (id > 0)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS rule_audit_set_idx ON seo_agent.rule_audit_log (rule_set_id, created_at DESC);"
    )

    # 视图
    op.execute(
        """
        CREATE OR REPLACE VIEW seo_agent.v_active_rule_set AS
        SELECT *
        FROM   seo_agent.rule_sets
        WHERE  is_active = true
          AND  effective_at <= now();
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW seo_agent.v_active_rule_field_map AS
        SELECT m.*
        FROM   seo_agent.rule_field_map m
        JOIN   seo_agent.v_active_rule_set s ON s.id = m.rule_set_id;
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS seo_agent.v_active_rule_field_map;")
    op.execute("DROP VIEW IF EXISTS seo_agent.v_active_rule_set;")
    op.execute("DROP TABLE IF EXISTS seo_agent.rule_audit_log CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS seo_agent.check_default_value_type();")
    op.execute("DROP TABLE IF EXISTS seo_agent.rule_field_map CASCADE;")
    op.execute("DROP TABLE IF EXISTS seo_agent.rule_sets CASCADE;")