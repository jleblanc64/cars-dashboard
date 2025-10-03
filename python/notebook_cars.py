import pandas as pd
from ipydatagrid import DataGrid
from IPython.core.display_functions import display
from ipywidgets import RadioButtons, widgets
from ipystream.stream import Stream, WidgetCurrentsChildren, Handle
from ipystream.renderer import plotly_fig_to_html
import plotly.graph_objects as go

# Monkey-patch display_or_update_with_print()
_original_display_or_update = WidgetCurrentsChildren.display_or_update

def display_or_update_with_print(self, widget):
    id = self.display_id(self.current_idx)
    h = Handle(idx=self.current_idx, w=self, display_id=id, cache=self.cache)

    is_update = self.current_idx < len(self.currents)
    if is_update:
        existing = self.currents[self.current_idx]
        # in this case re use existing, as it is certainly observed (eg. SelectMultiple, RadioButtons)
        if hasattr(existing, "options") and hasattr(existing, "value"):
            opts = widget.options
            value = widget.value

            with existing.hold_trait_notifications():
                existing.options = opts
            existing.value = value

            self.current_idx = self.current_idx + 1
            return h
        elif hasattr(existing, "value"):
            value = widget.value

            existing.value = value
            self.current_idx = self.current_idx + 1
            return h
        elif hasattr(existing, "children"):
            existing.children = widget.children

        elif self.vertical:
            h.update(widget)

    else:
        self.currents.append(None)

    self.currents[self.current_idx] = widget
    self.current_idx = self.current_idx + 1
    return h

WidgetCurrentsChildren.display_or_update = display_or_update_with_print


def run():
    df = pd.read_excel('cars.xlsx')
    cars = df.to_dict(orient='list')
    dicts = df.to_dict(orient='records')
    display(DataGrid(df, auto_fit_columns=True, layout={'height': '160px', 'width': 'auto'}, selection_mode="cell"))

    def couleurs(w):
        w.cache["marque"] = w.parents[0].value
        dicts_filt = [d for d in dicts if d["Marque"] == w.cache["marque"]]
        opts = sorted(list(set([d["Couleur"] for d in dicts_filt])))
        select = widgets.SelectMultiple(options=opts, value=opts, layout={'height': '50px'})
        w.display_or_update(select)

    def annees(w):
        dicts_filt = [d for d in dicts if d["Marque"] == w.cache["marque"] and d["Couleur"] in w.parents[0].value]
        annees = [d["Année"] for d in dicts_filt]

        annees_count = {k: 0 for k in annees}
        for a in annees: annees_count[a] = annees_count[a] + 1
        annees = sorted(list(set(annees)))
        counts = [annees_count[a] for a in annees]

        fig = go.Figure(data=[go.Pie(labels=annees, values=counts, textinfo="value", hoverinfo="label",  domain=dict(x=[0.05, 0.95], y=[0.05, 0.95]))])
        fig.update_layout(width=300,height=250,margin=dict(l=0, r=0, t=0, b=0))
        w.display_or_update(plotly_fig_to_html(fig))

        # datagrid
        df = pd.DataFrame({"annees": annees, "counts": counts})
        grid = DataGrid(
            df,
            selection_mode="cell",
            base_column_size=200,
            base_row_header_size=300,
            layout={"height": "250px"},
        )
        w.display_or_update(widgets.VBox([grid]))

    s = Stream()
    wi = RadioButtons(options=sorted(list(set(cars["Marque"]))))
    s.register(1, [lambda x: wi], title="Marque")
    s.register(2, updater=couleurs, title="Couleur (to select multiple, hold CTRL)")
    s.register(3, updater=annees, title="Années", vertical=True)
    s.display_registered()