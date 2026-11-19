// 온톨로지: 부대–장비–지역–활동–징후 + 문서(근거)
CREATE CONSTRAINT unit_name IF NOT EXISTS FOR (n:Unit) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT equipment_name IF NOT EXISTS FOR (n:Equipment) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT location_name IF NOT EXISTS FOR (n:Location) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT activity_name IF NOT EXISTS FOR (n:Activity) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT indicator_id IF NOT EXISTS FOR (n:Indicator) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT document_id IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE;
