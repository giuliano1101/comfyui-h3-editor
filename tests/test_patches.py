import copy
import unittest

import patches
import project as project_module
from project import (MODELS, PROFILES, apply_op, clean_patches, describe, new_project, plan_render, project_patches,
                     render_graph, render_models, rest_patches, set_project)

HYPERFLOW = set(PROFILES["hyperflow"]["unet"] + PROFILES["hyperflow"]["unet_text"])
FINALS = {MODELS["latent_upscaler"], MODELS["lora"]}

# Node definitions in the shape of ComfyUI's GET /object_info. The names are made up: the pack names no other pack's node.
ATTENTION = {
    "input": {"required": {
        "model": ["MODEL", {"tooltip": "The model to patch."}],
        "chunks": ["INT", {"default": 4, "min": 1, "max": 64, "step": 1, "tooltip": "How many pieces attention runs in."}],
        "ratio": ["FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.05}],
        "enabled": ["BOOLEAN", {"default": True}],
        "mode": [["fast", "exact"], {"default": "exact"}],
        "blocks": ["STRING", {"default": "", "multiline": False}]},
        "optional": {"mask": ["MASK"], "bias": ["FLOAT", {"default": 0.0, "min": -1.0, "max": 1.0}],
                     "wired": ["INT", {"default": 0, "forceInput": True}]},
        "hidden": {"unique_id": "UNIQUE_ID"}},
    "output": ["MODEL"], "output_is_list": [False], "display_name": "Sparse Attention", "category": "model_patches/video",
    "description": "Runs attention in pieces.", "python_module": "custom_nodes.some-attention-pack",
}
CACHE = {
    "input": {"required": {"model": ["MODEL"], "threshold": ["FLOAT", {"default": 0.2, "min": 0.0, "max": 3.0}]}},
    "output": ["MODEL"], "display_name": "Step Cache", "category": "advanced/debug/model", "python_module": "comfy_extras.nodes_cache",
}
# A newer node writes a menu as the type COMBO with its options beside it.
COMPILE = {
    "input": {"required": {"model": ["MODEL", {}], "backend": ["COMBO", {"options": ["inductor", "cudagraphs"], "default": "inductor"}]}},
    "output": ["MODEL"], "display_name": "Compile Model", "category": "_for_testing", "python_module": "nodes",
}


def known(**nodes):
    return {row["node"]: row for row in patches.listing(nodes)}


INSTALLED = known(SparseAttention=ATTENTION, StepCache=CACHE, CompileModel=COMPILE)


def clip(**extra):
    base = {"id": "c1", "action": "A keeper walks to the window.", "seconds": 5, "takes": []}
    base.update(extra)
    return base


def changed(info, **inputs):
    """A definition with some of its required inputs replaced, or removed with None."""
    out = copy.deepcopy(info)
    for name, spec in inputs.items():
        if spec is None:
            del out["input"]["required"][name]
        else:
            out["input"]["required"][name] = spec
    return out


class DefinitionTest(unittest.TestCase):
    def test_a_node_with_one_model_and_plain_values_is_a_patch(self):
        made = patches.definition("SparseAttention", ATTENTION)
        self.assertEqual((made["node"], made["title"], made["category"], made["pack"], made["model"]),
                         ("SparseAttention", "Sparse Attention", "model_patches/video", "some-attention-pack", "model"))
        self.assertEqual(made["about"], "Runs attention in pieces.")
        controls = {c["name"]: c for c in made["controls"]}
        # The model wire, the optional socket and the optional input that is only ever wired are not controls.
        self.assertEqual(list(controls), ["chunks", "ratio", "enabled", "mode", "blocks", "bias"])
        self.assertEqual(controls["chunks"], {"name": "chunks", "kind": "int", "default": 4, "min": 1, "max": 64, "step": 1,
                                              "tooltip": "How many pieces attention runs in."})
        self.assertEqual(controls["ratio"], {"name": "ratio", "kind": "float", "default": 0.5, "min": 0.0, "max": 1.0, "step": 0.05})
        self.assertEqual(controls["enabled"], {"name": "enabled", "kind": "bool", "default": True})
        self.assertEqual(controls["mode"], {"name": "mode", "kind": "choice", "default": "exact", "options": ["fast", "exact"]})
        self.assertEqual(controls["blocks"], {"name": "blocks", "kind": "text", "default": ""})
        self.assertTrue(controls["bias"]["optional"])

    def test_a_menu_written_as_combo_and_missing_defaults(self):
        made = patches.definition("CompileModel", COMPILE)
        self.assertEqual(made["controls"], [{"name": "backend", "kind": "choice", "default": "inductor", "options": ["inductor", "cudagraphs"]}])
        self.assertEqual(made["pack"], "ComfyUI")
        bare = patches.definition("Bare", changed(CACHE, threshold=["FLOAT"], count=["INT", {"min": 2}], on=["BOOLEAN"],
                                                  name=["STRING", {"multiline": True}], pick=[["a", "b"]]))
        self.assertEqual([(c["name"], c["default"]) for c in bare["controls"]],
                         [("threshold", 0), ("count", 2), ("on", False), ("name", ""), ("pick", "a")])
        self.assertTrue(bare["controls"][3]["multiline"])
        # A node with no display name goes by its id.
        self.assertEqual(patches.definition("Bare", dict(CACHE, display_name=""))["title"], "Bare")

    def test_what_is_not_a_patch(self):
        not_patches = {
            "two outputs": dict(ATTENTION, output=["MODEL", "LATENT"]),
            "another output": dict(ATTENTION, output=["LATENT"]),
            "no output": dict(ATTENTION, output=[]),
            "a list of models": dict(ATTENTION, output_is_list=[True]),
            "no model input": changed(ATTENTION, model=None),
            "two model inputs": changed(ATTENTION, other=["MODEL"]),
            "a required socket": changed(ATTENTION, vae=["VAE"]),
            "a required latent": changed(ATTENTION, latent=["LATENT", {}]),
            "a menu of several": changed(ATTENTION, picks=["COMBO", {"options": ["a", "b"], "multiselect": True}]),
            "a model that is optional": {"input": {"required": {"amount": ["FLOAT", {}]}, "optional": {"model": ["MODEL"]}}, "output": ["MODEL"]},
            "a second, optional model": dict(ATTENTION, input={"required": ATTENTION["input"]["required"], "optional": {"base": ["MODEL"]}}),
            "no definition": None,
            "no inputs": {"output": ["MODEL"]},
        }
        for why, info in not_patches.items():
            with self.subTest(why):
                self.assertIsNone(patches.definition("Node", info))

    def test_the_listing_puts_h3_and_attention_first_and_leaves_out_lora_loaders_and_the_packs_own(self):
        lora = {"input": {"required": {"model": ["MODEL"], "lora_name": [["a.safetensors"]], "strength_model": ["FLOAT", {"default": 1.0}]}},
                "output": ["MODEL"], "display_name": "Load LoRA (Model Only)", "category": "loaders", "python_module": "nodes"}
        rows = patches.listing({
            "StepCache": CACHE, "CompileModel": COMPILE, "SparseAttention": ATTENTION, "LoraLoaderModelOnly": lora,
            "ZetaChunk": dict(CACHE, display_name="Zeta Chunk", category="MiniMax/H3"),
            "H3OwnNode": dict(CACHE, display_name="Own"), "Sampler": dict(CACHE, output=["LATENT"]),
            "MathPatch": dict(CACHE, display_name="Graph3 Tweak"),
        }, skip={"H3OwnNode"})
        self.assertEqual([row["node"] for row in rows], ["SparseAttention", "ZetaChunk", "CompileModel", "MathPatch", "StepCache"])


class ValuesTest(unittest.TestCase):
    attention = INSTALLED["SparseAttention"]

    def test_defaults_fill_what_the_project_leaves_out(self):
        self.assertEqual(patches.values({"inputs": {"chunks": 8}}, self.attention),
                         {"chunks": 8, "ratio": 0.5, "enabled": True, "mode": "exact", "blocks": "", "bias": 0.0})
        self.assertEqual(patches.values({}, self.attention)["chunks"], 4)
        # A whole number may arrive as 8.0, and a number for a float as a whole one.
        made = patches.values({"inputs": {"chunks": 8.0, "ratio": 1}}, self.attention)
        self.assertEqual((made["chunks"], type(made["chunks"]), made["ratio"], type(made["ratio"])), (8, int, 1.0, float))

    def test_a_bad_value_is_refused_by_name(self):
        for inputs, words in (({"chunks": 100}, "Sparse Attention: chunks must be from 1 to 64, not 100"),
                              ({"chunks": 0}, "from 1 to 64"), ({"chunks": 2.5}, "chunks must be a whole number"),
                              ({"chunks": True}, "whole number"), ({"chunks": "4"}, "whole number"),
                              ({"ratio": 1.5}, "ratio must be from 0.0 to 1.0"), ({"ratio": "half"}, "ratio must be a number"),
                              ({"enabled": 1}, "enabled must be true or false"), ({"mode": "slow"}, "mode must be one of fast, exact, not 'slow'"),
                              ({"blocks": 3}, "blocks must be text"), ({"chunk": 8}, "has no input named chunk. It takes: chunks, ratio")):
            with self.subTest(inputs):
                with self.assertRaises(ValueError) as caught:
                    patches.values({"inputs": inputs}, self.attention)
                self.assertIn(words, str(caught.exception))

    def test_limits_on_one_side_and_empty_menus(self):
        node = patches.definition("N", changed(CACHE, low=["INT", {"default": 1, "min": 1}], menu=[[]]))
        with self.assertRaises(ValueError) as caught:
            patches.values({"inputs": {"low": 0}}, node)
        self.assertIn("low must be 1 or more, not 0", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            patches.values({}, node)
        self.assertIn("menu has nothing to choose from", str(caught.exception))


class CleanTest(unittest.TestCase):
    def test_patches_are_checked_when_they_are_set(self):
        project = new_project("x")
        for bad in ("SparseAttention", [{"inputs": {}}], [{"node": ""}], [{"node": 3}], [{"node": "A", "strength": 1}],
                    [{"node": "A", "inputs": [1]}], [{"node": "A", "inputs": {"x": [1]}}], [{"node": "A", "inputs": {"x": None}}],
                    [{"node": "A", "id": "p1"}, {"node": "B", "id": "p1"}], [{"node": "N%s" % n} for n in range(9)]):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    set_project(project, {"patches": bad})
        self.assertNotIn("patches", project)
        set_project(project, {"patches": [{"node": "SparseAttention", "inputs": {"chunks": 8}}, {"node": "NotInstalled", "on": False}]})
        first, second = project["patches"]
        self.assertEqual((first["node"], first["inputs"], first["on"]), ("SparseAttention", {"chunks": 8}, True))
        self.assertEqual((second["node"], second["inputs"], second["on"]), ("NotInstalled", {}, False))
        self.assertTrue(first["id"] and first["id"] != second["id"])
        # A row sent back with its id keeps it, and the same node can be listed twice.
        set_project(project, {"patches": [dict(first, inputs={"chunks": 2}), {"node": "SparseAttention"}]})
        self.assertEqual(project["patches"][0]["id"], first["id"])
        self.assertEqual(clean_patches([]), [])

    def test_a_patch_can_only_say_so_much(self):
        for bad in ([{"node": "N" * 201}], [{"node": "A", "inputs": {"x" * 201: 1}}], [{"node": "A", "inputs": {"x": "y" * 4001}}],
                    [{"node": "A", "inputs": {"i%s" % n: n for n in range(65)}}]):
            with self.assertRaises(ValueError):
                clean_patches(bad)
        self.assertEqual(clean_patches([{"node": "A", "inputs": {"x": "y" * 4000}}])[0]["inputs"]["x"], "y" * 4000)
        # A project file written by hand is held to the same length when it renders.
        with self.assertRaises(ValueError) as caught:
            patches.values({"inputs": {"blocks": "1," * 2001}}, INSTALLED["SparseAttention"])
        self.assertIn("blocks takes up to 4000 characters", str(caught.exception))

    def test_a_project_from_somewhere_else_has_its_patches_switched_off(self):
        project = new_project("x")
        self.assertEqual(rest_patches(project), [])
        self.assertNotIn("patches", project)
        set_project(project, {"patches": [{"node": "StepCache", "inputs": {"threshold": 0.4}}, {"node": "CompileModel", "on": False},
                                          {"node": "SparseAttention"}]})
        ids = [row["id"] for row in project["patches"]]
        self.assertEqual(rest_patches(project), ["StepCache", "SparseAttention"])
        self.assertEqual([(row["id"], row["on"]) for row in project["patches"]], [(i, False) for i in ids])
        self.assertEqual(project["patches"][0]["inputs"], {"threshold": 0.4})
        graph = render_graph(project, "x", clip(), "t1", "draft", 3, None, INSTALLED)
        self.assertNotIn("120", graph)
        # A list no project could hold is dropped, not switched off.
        for junk in ("StepCache", [{"node": "A", "inputs": {"x": "y" * 5000}}], [{"node": "N%s" % n} for n in range(9)], [3]):
            project["patches"] = junk
            self.assertEqual(rest_patches(project), [])
            self.assertNotIn("patches", project)

    def test_an_agent_sets_them_in_a_batch_and_reads_them_in_the_summary(self):
        project = new_project("x")
        apply_op(project, {"op": "set_project", "fields": {"patches": [{"node": "StepCache", "inputs": {"threshold": 0.4}, "on": False}]}}, {})
        self.assertEqual(describe(project)["patches"], [{"node": "StepCache", "inputs": {"threshold": 0.4}, "on": False}])

    def test_two_writers_merge_by_row(self):
        base = new_project("x")
        set_project(base, {"patches": [{"node": "StepCache"}, {"node": "SparseAttention"}]})
        mine, theirs = copy.deepcopy(base), copy.deepcopy(base)
        mine["patches"][0]["inputs"] = {"threshold": 0.4}
        theirs["patches"][1]["on"] = False
        merged = project_module.merge(base, mine, theirs)
        self.assertEqual([(r["inputs"], r["on"]) for r in merged["patches"]], [({"threshold": 0.4}, True), ({}, False)])


class GraphTest(unittest.TestCase):
    def wire(self, graph):
        """The nodes on the model wire, from the loader to the node both guiders sample from."""
        chain, at = [], "9"
        while at:
            chain.append(at)
            at = (graph[at]["inputs"].get("model") or [None])[0]
        return chain[::-1]

    def test_no_patch_leaves_the_graph_as_it_was(self):
        project = new_project("x")
        plain = render_graph(project, "x", clip(), "t1", "draft", 3)
        self.assertEqual(render_graph(project, "x", clip(), "t1", "draft", 3, None, INSTALLED), plain)
        set_project(project, {"patches": []})
        self.assertEqual(render_graph(project, "x", clip(), "t1", "draft", 3, None, INSTALLED), plain)
        self.assertNotIn("patches", render_models(plain))
        # Without the installed nodes there is no definition to wire a patch from.
        set_project(project, {"patches": [{"node": "StepCache"}]})
        self.assertEqual(render_graph(project, "x", clip(), "t1", "draft", 3), plain)

    def test_one_patch_sits_between_the_loader_and_the_shift(self):
        project = new_project("x")
        set_project(project, {"patches": [{"node": "SparseAttention", "inputs": {"chunks": 8, "mode": "fast"}}]})
        graph = render_graph(project, "x", clip(), "t1", "draft", 3, None, INSTALLED)
        self.assertEqual(graph["120"], {"class_type": "SparseAttention", "inputs": {
            "model": ["1", 0], "chunks": 8, "ratio": 0.5, "enabled": True, "mode": "fast", "blocks": "", "bias": 0.0}})
        self.assertEqual(graph["5"]["inputs"]["model"], ["120", 0])
        self.assertEqual(self.wire(graph), ["1", "120", "5", "9"])
        self.assertEqual(graph["13"]["inputs"]["model"], ["9", 0])
        self.assertEqual(render_models(graph)["patches"], [{"node": "SparseAttention", "inputs": {
            "chunks": 8, "ratio": 0.5, "enabled": True, "mode": "fast", "blocks": "", "bias": 0.0}}])

    def test_two_patches_stack_in_order_after_every_lora(self):
        project = new_project("x")
        set_project(project, {"loras": [{"file": "film.safetensors", "strength": 0.8}, {"file": "grain.safetensors"}],
                              "patches": [{"node": "StepCache", "inputs": {"threshold": 0.4}}, {"node": "CompileModel"}]})
        draft = render_graph(project, "x", clip(loras=[{"file": "hero.safetensors"}]), "t1", "draft", 3, None, INSTALLED)
        self.assertEqual(self.wire(draft), ["1", "100", "101", "102", "120", "121", "5", "9"])
        self.assertEqual(draft["120"], {"class_type": "StepCache", "inputs": {"model": ["102", 0], "threshold": 0.4}})
        self.assertEqual(draft["121"], {"class_type": "CompileModel", "inputs": {"model": ["120", 0], "backend": "inductor"}})
        used = render_models(draft)
        self.assertEqual([row["file"] for row in used["loras"]], ["film.safetensors", "grain.safetensors", "hero.safetensors"])
        self.assertEqual(used["patches"], [{"node": "StepCache", "inputs": {"threshold": 0.4}},
                                           {"node": "CompileModel", "inputs": {"backend": "inductor"}}])

    def test_both_passes_of_a_final_sample_with_the_patches(self):
        project = new_project("x")
        set_project(project, {"loras": [{"file": "film.safetensors"}],
                              "patches": [{"node": "StepCache"}, {"node": "SparseAttention"}]})
        final = render_graph(project, "x", clip(), "t1", "final", 3, HYPERFLOW | FINALS | {"film.safetensors"}, INSTALLED)
        # The Realism Slider, the project's LoRA, then the patches, on the one model both guiders take.
        self.assertEqual(self.wire(final), ["1", "95", "100", "120", "121", "5", "9"])
        self.assertIn("90", final)
        self.assertEqual((final["13"]["inputs"]["model"], final["88"]["inputs"]["model"]), (["9", 0], ["9", 0]))
        # HyperFlow reads its schedule from the same patched model.
        self.assertEqual(final["12"]["inputs"]["model"], ["5", 0])
        job, _ = plan_render(dict(project, clips=[clip()]), "x", "c1", "final", 3, HYPERFLOW | FINALS | {"film.safetensors"}, patches=INSTALLED)
        self.assertEqual([row["node"] for row in job["models"]["patches"]], ["StepCache", "SparseAttention"])
        self.assertEqual(job["models"]["loras"], [{"file": "film.safetensors", "strength": 1.0}])

    def test_a_patch_that_is_off_or_not_installed_is_skipped(self):
        project = new_project("x")
        set_project(project, {"patches": [{"node": "StepCache", "on": False}, {"node": "NotInstalled", "inputs": {"speed": 2}},
                                          {"node": "CompileModel"}]})
        graph = render_graph(project, "x", clip(), "t1", "draft", 3, None, INSTALLED)
        self.assertEqual(self.wire(graph), ["1", "120", "5", "9"])
        self.assertEqual(graph["120"]["class_type"], "CompileModel")
        # The project keeps all three, for a machine that has the pack.
        self.assertEqual([row["node"] for row in project["patches"]], ["StepCache", "NotInstalled", "CompileModel"])
        self.assertEqual([known["node"] for _, known in project_patches(project, INSTALLED)], ["CompileModel"])
        # A project file written by hand cannot break a render.
        project["patches"] = ["StepCache", {"node": 3}, {"inputs": {}}, {"node": "StepCache", "inputs": "fast"}]
        graph = render_graph(project, "x", clip(), "t1", "draft", 3, None, INSTALLED)
        self.assertEqual(graph["120"], {"class_type": "StepCache", "inputs": {"model": ["1", 0], "threshold": 0.2}})

    def test_a_bad_value_refuses_the_render_and_not_when_the_patch_is_off(self):
        project = dict(new_project("x"), clips=[clip()])
        set_project(project, {"patches": [{"node": "SparseAttention", "inputs": {"chunks": 100}}]})
        with self.assertRaises(ValueError) as caught:
            plan_render(project, "x", "c1", "draft", 3, patches=INSTALLED)
        self.assertEqual(str(caught.exception), "Sparse Attention: chunks must be from 1 to 64, not 100")
        project["patches"][0]["on"] = False
        job, graph = plan_render(project, "x", "c1", "draft", 3, patches=INSTALLED)
        self.assertNotIn("120", graph)
        self.assertNotIn("patches", job["models"])


if __name__ == "__main__":
    unittest.main()
