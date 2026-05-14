/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class SaleKitOverviewAction extends Component {
  static template = "sale_kit_report.SaleKitBreakdownTable";

  setup() {
    this.orm = useService("orm");
    this.action = useService("action");

    this.state = useState({
      breakdownData: [],
      totals: {
        untaxed: 0.0,
        tax: 0.0,
        total: 0.0,
      },
      expandedLines: {},
    });

    this.orderId = this.props.action.context.default_order_id;

    onWillStart(async () => {
      await this.loadBreakdown();
    });
  }

  async loadBreakdown() {
    if (this.orderId) {
      const result = await this.orm.call(
        "sale.order",
        "get_kit_breakdown_data",
        [this.orderId],
      );
      this.state.breakdownData = result.lines || [];
      this.state.totals.untaxed = result.amount_untaxed || 0.0;
      this.state.totals.tax = result.amount_tax || 0.0;
      this.state.totals.total = result.amount_total || 0.0;
    }
  }

  toggleExpand(lineId) {
    this.state.expandedLines[lineId] = !this.state.expandedLines[lineId];
  }

  get visibleLines() {
    return this.state.breakdownData.filter((line) => {
      if (line.type === "line") return true;

      let parentId = line.parent_id;
      while (parentId) {
        if (!this.state.expandedLines[parentId]) return false;
        const parentLine = this.state.breakdownData.find(
          (l) => l.id === parentId,
        );
        parentId = parentLine ? parentLine.parent_id : null;
      }
      return true;
    });
  }

  goBack() {
    this.action.doAction({
      type: "ir.actions.act_window",
      res_model: "sale.order",
      res_id: this.orderId,
      views: [[false, "form"]],
      target: "current",
    });
  }
}

registry
  .category("actions")
  .add("sale_kit_overview_action", SaleKitOverviewAction);
