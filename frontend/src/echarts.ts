// echarts 按需注册（官方 tree-shakeable 接口）——只带本项目用到的图表与组件，
// 避免 import 'echarts' 全量打包。缺注册的图表在运行时以 console error 报错而非抛异常，
// 因此注册清单与使用处必须同步维护。
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import {
  LegendComponent,
  TooltipComponent,
  GridComponent,
} from 'echarts/components'
import { LabelLayout } from 'echarts/features'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  LegendComponent,
  TooltipComponent,
  GridComponent,
  LabelLayout,
  CanvasRenderer,
])

export default echarts
